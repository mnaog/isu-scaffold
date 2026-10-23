"""lane state-logがアプリへ写すGoのprofileの窓口（config/profile/profile*.go）を、単独のGoのmoduleで確かめる。

isuscopeのgo-profile collectorとの約束（start・stopの形、base64のpprof）を守ること、CPUのsampleに
APIごとのlabelが付くこと、mutexの取り合いが数えられること、環境変数で止められることを見る。
goが無い端末では飛ばす。
"""
import base64
import gzip
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted((ROOT / 'config/profile').glob('profile*.go'))

MAIN = r'''
package main

import (
	"crypto/sha256"
	"net/http"
	"os"
	"sync"
	"time"
)

var (
	shared sync.Mutex
	total  int
)

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/rides/", func(w http.ResponseWriter, r *http.Request) {
		// CPUを使い、同じmutexを取り合う。
		sum := sha256.Sum256([]byte(r.URL.Path))
		for index := 0; index < 20000; index++ {
			sum = sha256.Sum256(sum[:])
		}
		shared.Lock()
		total++
		time.Sleep(200 * time.Microsecond)
		shared.Unlock()
		w.Write(sum[:4])
	})
	server := &http.Server{Addr: os.Args[1], Handler: profileMiddleware(mux)}
	server.ListenAndServe()
}
'''

UNIT_TESTS = r'''
package main

import "testing"

func TestRoutesAreRoundedLikeIsuscopeSuggests(t *testing.T) {
	for path, want := range map[string]string{
		"/api/app/rides/01JAB3C4D5E6F7G8H9J0K1M2N3/evaluation": "/api/app/rides/:key/evaluation",
		"/api/chair/rides/12/status":                            "/api/chair/rides/:id/status",
		"/api/app/nearby-chairs":                                "/api/app/nearby-chairs",
		"/api/owner/chairs/0b4a1e1c-6f0e-4a5b-9b1e-1f2a3b4c5d6e": "/api/owner/chairs/:uuid",
		"/assets/index-0123456789abcdef.js":                     "/assets/index-0123456789abcdef.js",
	} {
		if got := profileRoute(path); got != want {
			t.Errorf("%s: got %s, want %s", path, got, want)
		}
	}
}

func TestDeclaredRoutesComeFirst(t *testing.T) {
	saved := profileRoutes
	defer func() { profileRoutes = saved }()
	profileRoutes = profileParseRoutes(" /api/app/rides/{ride_id}/evaluation ; bogus; /api/chair/*")
	if got := profileRoute("/api/app/rides/abc/evaluation"); got != "/api/app/rides/{ride_id}/evaluation" {
		t.Fatal(got)
	}
	if got := profileRoute("/api/chair/coordinate"); got != "/api/chair/*" {
		t.Fatal(got)
	}
	if got := profileRoute("/api/chair/"); got != "/api/chair/" {
		t.Fatal(got)
	}
}
'''


def go_env():
    return {**os.environ, 'GOTOOLCHAIN': 'local', 'GOFLAGS': '', 'GO111MODULE': 'on'}


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def request(url, method='GET'):
    with urllib.request.urlopen(urllib.request.Request(url, method=method), timeout=30) as response:
        return response.read()


def wait_for(url):
    for _ in range(100):
        try:
            return request(url)
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.05)
    raise AssertionError(f'{url} did not answer')


@unittest.skipUnless(shutil.which('go'), 'go is not installed')
class ProfileEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.module = Path(cls.tmp.name) / 'module'
        cls.module.mkdir()
        (cls.module / 'go.mod').write_text('module profile_check\n\ngo 1.22\n')
        for source in SOURCES:
            shutil.copyfile(source, cls.module / source.name)
        (cls.module / 'main.go').write_text(MAIN)
        (cls.module / 'profile_test.go').write_text(UNIT_TESTS)
        cls.env = go_env()
        cls.binary = Path(cls.tmp.name) / 'profile_check'
        subprocess.run(['go', 'build', '-o', str(cls.binary), '.'], cwd=cls.module, env=cls.env, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def start_app(self, **variables):
        app_port, profile_port = free_port(), free_port()
        env = {**self.env, 'ISUCON_PROFILE_ADDR': f'127.0.0.1:{profile_port}', **variables}
        process = subprocess.Popen([str(self.binary), f'127.0.0.1:{app_port}'], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        return f'http://127.0.0.1:{app_port}', f'http://127.0.0.1:{profile_port}'

    def test_the_package_passes_go_vet_and_its_unit_tests(self):
        result = subprocess.run(['go', 'vet', '.'], cwd=self.module, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(['go', 'test', '-count=1', '-run', 'Route', '.'], cwd=self.module,
                                env={**self.env, 'ISUCON_PROFILE_ADDR': ''}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_start_and_stop_return_the_profiles_of_the_window_with_route_labels(self):
        app, profile = self.start_app(ISUCON_PROFILE_MUTEX_FRACTION='1')
        wait_for(f'{profile}/debug/pprof/')
        wait_for(f'{app}/api/rides/1')
        self.assertIn(b'started run-1', request(f'{profile}/debug/isuscope/start?run=run-1', 'POST'))
        deadline = time.time() + 1.5
        while time.time() < deadline:
            for ride in ('01JAB3C4D5E6F7G8H9J0K1M2N3', '42'):
                request(f'{app}/api/rides/{ride}')
        text = request(f'{profile}/debug/isuscope/stop?run=run-1', 'POST').decode()
        header, *lines = text.splitlines()
        fields = dict(field.split('=', 1) for field in header.split()[2:])
        self.assertEqual(header.split()[:2], ['isuscope-go-profile', '1'])
        self.assertEqual(fields['run'], 'run-1')
        self.assertGreater(float(fields['stopped']), float(fields['started']))
        profiles = {}
        for line in lines:
            kind, kind_name, data = line.split(' ', 2)
            self.assertEqual(kind, 'profile')
            profiles[kind_name] = base64.b64decode(data)
        for name in ('cpu', 'mutex-base', 'mutex', 'allocs-base', 'allocs', 'goroutine'):
            self.assertIn(name, profiles)
            gzip.decompress(profiles[name])
        cpu = Path(self.tmp.name) / 'cpu.pprof'
        cpu.write_bytes(profiles['cpu'])
        tags = subprocess.run(['go', 'tool', 'pprof', '-tags', str(cpu)], env=self.env,
                              capture_output=True, text=True, check=True).stdout
        self.assertIn('/api/rides/:key', tags)
        self.assertIn('/api/rides/:id', tags)
        self.assertIn('GET', tags)
        # 同じrunをもう一度止めることはできない。
        with self.assertRaises(urllib.error.HTTPError) as raised:
            request(f'{profile}/debug/isuscope/stop?run=run-1', 'POST')
        self.assertEqual(raised.exception.code, 409)

    def test_a_stop_for_another_run_is_refused(self):
        _, profile = self.start_app()
        wait_for(f'{profile}/debug/pprof/')
        request(f'{profile}/debug/isuscope/start?run=a', 'POST')
        with self.assertRaises(urllib.error.HTTPError) as raised:
            request(f'{profile}/debug/isuscope/stop?run=b', 'POST')
        self.assertEqual(raised.exception.code, 409)
        self.assertTrue(request(f'{profile}/debug/isuscope/stop?run=a', 'POST').startswith(b'isuscope-go-profile'))

    def test_the_flight_recorder_answers_only_when_asked_for(self):
        _, profile = self.start_app()
        wait_for(f'{profile}/debug/pprof/')
        with self.assertRaises(urllib.error.HTTPError) as raised:
            request(f'{profile}/debug/isuscope/flight')
        self.assertEqual(raised.exception.code, 404)
        version = subprocess.run(['go', 'env', 'GOVERSION'], env=self.env, capture_output=True, text=True).stdout
        minor = int(version.strip().removeprefix('go').split('.')[1])
        if minor < 25:
            self.skipTest('the flight recorder needs Go 1.25')
        _, profile = self.start_app(ISUCON_FLIGHT_RECORDER='5')
        wait_for(f'{profile}/debug/pprof/')
        trace = request(f'{profile}/debug/isuscope/flight')
        self.assertTrue(trace.startswith(b'go 1.'), trace[:16])

    def test_an_empty_address_serves_nothing(self):
        port = free_port()
        app = subprocess.Popen([str(self.binary), f'127.0.0.1:{free_port()}'],
                               env={**self.env, "ISUCON_PROFILE_ADDR": ""}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(app.wait)
        self.addCleanup(app.kill)
        time.sleep(0.5)
        with socket.socket() as client:
            self.assertNotEqual(client.connect_ex(('127.0.0.1', port)), 0)


class ProfileLaneTest(unittest.TestCase):
    def test_the_state_log_lane_copies_the_files_and_the_export_carries_them(self):
        lanes = json.loads((ROOT / 'config/lanes.json').read_text())
        prompt = (ROOT / lanes['lanes']['state-log']['prompt']).read_text()
        self.assertIn('config/profile/', prompt)
        self.assertIn('profileMiddleware', prompt)
        export = json.loads((ROOT / 'config/scaffold-export.json').read_text())['files']
        for source in SOURCES:
            self.assertIn(str(source.relative_to(ROOT)), export)


if __name__ == '__main__':
    unittest.main()
