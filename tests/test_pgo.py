"""scripts/pgo.pyが、isuscopeのrunに残ったGoのCPU profile（go-profile-stopの出力）をPGOのprofileにし、
`go build`がそれを使うことを確かめる。goかzstdが無い端末では飛ばす。"""
import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import importlib.util
spec = importlib.util.spec_from_file_location('pgo', ROOT / 'scripts/pgo.py')
pgo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pgo)

PROGRAM = r'''
package main

import (
	"crypto/sha256"
	"os"
	"runtime/pprof"
	"time"
)

func hot(data []byte) [32]byte {
	sum := sha256.Sum256(data)
	for index := 0; index < 1000; index++ {
		sum = sha256.Sum256(sum[:])
	}
	return sum
}

func main() {
	if len(os.Args) > 1 {
		file, _ := os.Create(os.Args[1])
		pprof.StartCPUProfile(file)
		deadline := time.Now().Add(700 * time.Millisecond)
		for time.Now().Before(deadline) {
			hot([]byte("x"))
		}
		pprof.StopCPUProfile()
		file.Close()
	}
}
'''


def go_env():
    return {**os.environ, 'GOTOOLCHAIN': 'local', 'GOFLAGS': '', 'GO111MODULE': 'on', 'CGO_ENABLED': '0'}


@unittest.skipUnless(shutil.which('go') and shutil.which('zstd'), 'go or zstd is not installed')
class PgoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.app = self.work / 'app'
        self.app.mkdir()
        (self.app / 'go.mod').write_text('module pgocheck\n\ngo 1.22\n')
        (self.app / 'main.go').write_text(PROGRAM)
        subprocess.run(['go', 'run', '.', str(self.work / 'cpu.pprof')], cwd=self.app, env=go_env(), check=True)
        self.runs = self.work / 'runs'

    def add_run(self, run_id, nodes):
        logs = self.runs / run_id / 'logs'
        logs.mkdir(parents=True)
        profile = base64.b64encode((self.work / 'cpu.pprof').read_bytes()).decode()
        for node in nodes:
            text = f'isuscope-go-profile 1 run={run_id} started=1 stopped=2\nprofile mutex AAAA\nprofile cpu {profile}\n'
            raw = logs / f'go-profile-stop-{node}-after-stdout'
            raw.write_text(text)
            subprocess.run(['zstd', '-q', '--rm', str(raw), '-o', str(raw) + '.zst'], check=True)

    def pgo(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'scripts/pgo.py'), *args, '--runs-dir', str(self.runs),
                               '--output', str(self.app / 'default.pgo')], capture_output=True, text=True)

    def test_the_profile_of_a_run_becomes_default_pgo_and_go_build_uses_it(self):
        self.add_run('01a16414-541c-73a3-87e0-eed0607d2d78', ['app3'])
        result = self.pgo('0607d2d78')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('app3', result.stdout)
        self.assertEqual((self.app / 'default.pgo').read_bytes(), (self.work / 'cpu.pprof').read_bytes())
        binary = self.work / 'built'
        subprocess.run(['go', 'build', '-o', str(binary), '.'], cwd=self.app, env=go_env(), check=True)
        settings = subprocess.run(['go', 'version', '-m', str(binary)], env=go_env(), capture_output=True,
                                  text=True, check=True).stdout
        self.assertIn('-pgo=', settings)
        self.assertIn('default.pgo', settings)

    def test_profiles_of_several_nodes_are_merged_and_latest_finds_the_newest(self):
        self.add_run('01a16400-0000-7000-8000-000000000001', ['app1'])
        self.add_run('01a16414-541c-73a3-87e0-eed0607d2d78', ['app2', 'app3'])
        (self.runs / '01a16420-0000-7000-8000-000000000009' / 'logs').mkdir(parents=True)
        result = self.pgo('latest')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('app2, app3', result.stdout)
        merged = subprocess.run(['go', 'tool', 'pprof', '-top', str(self.app / 'default.pgo')], env=go_env(),
                                capture_output=True, text=True, check=True).stdout
        self.assertIn('main.hot', merged)

    def test_a_run_without_a_profile_is_refused(self):
        self.add_run('01a16400-0000-7000-8000-000000000001', ['app1'])
        result = self.pgo('01a16400-0000-7000-8000-000000000001', '--node', 'app9')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('no Go CPU profile from app9', result.stderr)
        self.assertFalse((self.app / 'default.pgo').exists())


class CpuProfileTest(unittest.TestCase):
    def test_only_the_cpu_line_of_a_go_profile_output_is_taken(self):
        self.assertEqual(pgo.cpu_profile('isuscope-go-profile 1 run=a\nprofile mutex AQI=\nprofile cpu AwQ=\n'), b'\x03\x04')
        self.assertIsNone(pgo.cpu_profile('something else\nprofile cpu AwQ=\n'))
        self.assertIsNone(pgo.cpu_profile('isuscope-go-profile 1 run=a\n'))


if __name__ == '__main__':
    unittest.main()
