"""scripts/benchmark-sample.py turns a pending run's saved output into the verified sample."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
RUN_ID = '01a1351d-3a33-7ae0-831f-c978e03fc81f'
OUTPUT = """\
11:55:37 [ValidationScenario] start
11:55:38 [ADMIN] operator-only detail
11:55:41 [VALIDATION_ERR] prepare: timeout: Post "http://10.0.0.1/login": context deadline exceeded
11:55:41 [PASSED]: false
11:55:41 [SCORE] 0 (addition: 0, deduction: 0)
{"type":"isuscope.result","score":0,"pass":false,"messages":["benchmark exit status 0"]}
"""


@unittest.skipUnless(shutil.which('zstd'), 'needs zstd')
class BenchmarkSampleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('scripts/benchmark-contract.py', 'scripts/timeout-command.py', 'scripts/benchmark-sample.py',
                     '.isuscope/benchmark.sh'):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / name, self.root / name)
        (self.root / 'config').mkdir()
        shutil.copy(ROOT / '.isuscope/policy.toml', self.root / '.isuscope/policy.toml')
        # The scaffold's default patterns, with a local command the contract can review.
        env = (ROOT / 'config/benchmark.env').read_text()
        env = re.sub(r'^BENCHMARK_TRANSPORT=.*$', 'BENCHMARK_TRANSPORT=local', env, flags=re.M)
        env = re.sub(r'^BENCHMARK_COMMAND=.*$', "BENCHMARK_COMMAND='printf done'", env, flags=re.M)
        env = re.sub(r'^BENCHMARK_PROBE_COMMAND=.*$', "BENCHMARK_PROBE_COMMAND='true'", env, flags=re.M)
        (self.root / 'config/benchmark.env').write_text(env)
        subprocess.run(['bash', '-c', 'set -a; . config/benchmark.env; set +a; python3 ' + str(ROOT / 'tests/benchmark-contract-fixture.py')],
                       cwd=self.root, check=True)
        logs = self.root / 'isuscope-data/runs' / RUN_ID / 'logs'
        logs.mkdir(parents=True)
        subprocess.run(['zstd', '-q', '-o', str(logs / 'benchmark-stdout.zst')], input=OUTPUT.encode(), check=True)
        # isuscope list shows the run pending; rescore is recorded.
        (self.root / 'bin').mkdir()
        (self.root / 'bin/isuscope').write_text(
            '#!/bin/sh\nif [ "$1" = list ]; then echo \'{"runs":{"columns":["id","state"],"rows":[["' + RUN_ID + '","pending"]]}}\'; '
            'exit 0; fi\necho "isuscope $*" >> rescored\n')
        (self.root / 'bin/isuscope').chmod(0o755)

    def sample(self, *args):
        return subprocess.run(['python3', 'scripts/benchmark-sample.py', 'e03fc81f', *args], cwd=self.root,
                              capture_output=True, text=True,
                              env=dict(os.environ, PATH=f'{self.root / "bin"}{os.pathsep}{os.environ["PATH"]}'))

    def test_shows_what_the_default_patterns_read_without_changing_anything(self):
        before = (self.root / 'config/benchmark.env').read_text()
        completed = self.sample()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('score=0 pass=false', completed.stdout)
        self.assertNotIn('ADMIN', completed.stdout)
        self.assertEqual((self.root / 'config/benchmark.env').read_text(), before)
        self.assertFalse((self.root / 'rescored').exists())

    def test_default_patterns_read_key_value_results(self):
        # ISUCON14 ends with `msg=結果 pass=false スコア=N`; `pass=` alone read as PASS (practice-14-forth).
        for line, expected in (('msg=結果 pass=false スコア=0 種別エラー数=3', (0, False)),
                               ('msg=結果 pass=true スコア=850 種別エラー数=0', (850, True))):
            output = self.root / 'output.log'
            output.write_text('start\n' + line + '\n')
            completed = subprocess.run(['.isuscope/benchmark.sh', '--read', str(output)], cwd=self.root,
                                       capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual((result['score'], result['pass']), expected, line)

    def test_apply_needs_the_values_read_by_hand_to_agree(self):
        completed = self.sample('--apply', '--score', '64', '--pass', 'true')
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('fix them in config/benchmark.env', completed.stderr)
        self.assertNotIn("BENCHMARK_SAMPLE_SCORE=64", (self.root / 'config/benchmark.env').read_text())

    def test_apply_writes_the_sample_reseals_checks_and_rescores(self):
        completed = self.sample('--apply', '--score', '0', '--pass', 'false')
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        sample = (self.root / 'config/benchmark-sample.log').read_text()
        self.assertIn('[SCORE] 0', sample)
        self.assertNotIn('[ADMIN]', sample)
        self.assertNotIn('isuscope.result', sample)
        env = (self.root / 'config/benchmark.env').read_text()
        self.assertIn('BENCHMARK_SAMPLE_SCORE=0\n', env)
        self.assertIn('BENCHMARK_SAMPLE_PASS=false\n', env)
        self.assertEqual((self.root / 'rescored').read_text().strip(), f'isuscope rescore {RUN_ID}')
        checked = subprocess.run(['bash', '.isuscope/benchmark.sh', '--check'], cwd=self.root, capture_output=True, text=True,
                                 env=dict(os.environ, ISUSCOPE_PROJECT_ROOT=str(self.root)))
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertNotIn('not verified', checked.stderr)


if __name__ == '__main__':
    unittest.main()
