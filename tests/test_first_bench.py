import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class FirstBenchTests(unittest.TestCase):
    """make first-bench takes the baseline on the distributed build when it can, else deploys first."""

    # `isuscope list` answers from .local/runs.json (no runs by default) and is not an event.
    FAKE_LIST = ('#!/bin/sh\nif [ "$1" = list ]; then cat .local/runs.json 2>/dev/null || '
                 'echo \'{"runs":{"columns":["id","state"],"rows":[]}}\'; exit 0; fi\n')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        for folder in ('scripts', 'bin', 'webapp/go', 'config', '.local'):
            (self.repo / folder).mkdir(parents=True)
        for name in ('first-bench.sh', 'stage-lib.sh', 'progress.py', 'imported-state.py', 'tree-digest.py',
                     'check-deployed-commit.sh'):
            shutil.copy(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        for name in ('bench-check.sh', 'deploy.sh', 'benchmark-prepare.sh'):
            step = name.removesuffix('.sh')
            (self.repo / 'scripts' / name).write_text(
                f'#!/bin/sh\necho "{step}${{REQUIRE_ACTIVE:+ REQUIRE_ACTIVE=$REQUIRE_ACTIVE}}${{PREPARE_CHECK_ONLY:+ check-only}}" >> "$EVENTS"\n'
                f'[ "$FAIL" = {step} ] && exit 1\nexit 0\n')
        (self.repo / 'bin/isuscope').write_text(self.FAKE_LIST + 'echo "isuscope $*" >> "$EVENTS"\n')
        for path in [*(self.repo / 'scripts').glob('*.sh'), self.repo / 'bin/isuscope']:
            path.chmod(0o755)
        (self.repo / 'webapp/go/main.go').write_text('package main\n')
        (self.repo / 'config/sync.json').write_text(json.dumps({'items': [
            {'name': 'go-app', 'local': 'webapp/go', 'exclude': ['app']}]}))
        (self.repo / '.gitignore').write_text('.local/\nbin/\nevents\nwebapp/go/app\n')
        for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-qm', 'fixture']):
            subprocess.run(['git', '-C', str(self.repo), *args], check=True)
        self.events = self.repo / 'events'

    def tearDown(self):
        self.tmp.cleanup()

    def record_import(self):
        """What import.sh leaves: the digest the servers hold for each item."""
        (self.repo / 'webapp/go/app').write_text('distributed binary, never compared\n')
        digest = subprocess.run(['python3', 'scripts/tree-digest.py', 'webapp/go', '--exclude', 'app'],
                                cwd=self.repo, capture_output=True, text=True, check=True).stdout.strip()
        (self.repo / '.local/imported-state.json').write_text(json.dumps({'items': {
            'go-app': {'local': 'webapp/go', 'digest': digest, 'exclude': ['app']}}}))

    def run_first_bench(self, **env):
        environment = {k: v for k, v in os.environ.items()
                       if k not in ('ISUCON_LANE', 'PROGRESS_LOG', 'FAIL', 'FIRST_BENCH_MODE', 'REQUIRE_ACTIVE')}
        environment.update(PATH=f'{self.repo / "bin"}{os.pathsep}{os.environ["PATH"]}', EVENTS=str(self.events), **env)
        return subprocess.run(['bash', 'scripts/first-bench.sh'], cwd=self.repo, env=environment,
                              capture_output=True, text=True)

    def lines(self):
        return self.events.read_text().splitlines() if self.events.exists() else []

    def states(self):
        return [line.split('\t')[2] for line in (self.repo / '.local/progress.log').read_text().splitlines()]

    def head(self):
        return subprocess.run(['git', '-C', str(self.repo), 'rev-parse', 'HEAD'], capture_output=True,
                              text=True, check=True).stdout.strip()

    def test_baseline_on_the_distributed_build_then_deploy(self):
        self.record_import()
        result = self.run_first_bench(HYPOTHESIS='初期状態を記録する')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.lines(), ['bench-check', 'benchmark-prepare REQUIRE_ACTIVE=1 check-only',
                                        'isuscope survey-run --hypothesis 初期状態を記録する', 'deploy'])
        self.assertEqual((self.repo / '.local/current-import-commit').read_text().strip(), self.head())
        self.assertEqual(self.states(), ['started', 'checked', 'distributed', 'baseline-ready',
                                         'deployed', 'complete'])

    def fake_survey_run(self, state):
        """A survey-run that records one run in the given state and exits 1, as isuscope does for
        every result that is not a PASS."""
        runs = json.dumps({'runs': {'columns': ['id', 'state'], 'rows': [['new-run', state]]}})
        (self.repo / 'bin/isuscope').write_text(
            self.FAKE_LIST + 'echo "isuscope $*" >> "$EVENTS"\n'
            f"echo '{runs}' > .local/runs.json\nexit 1\n")

    def test_a_pending_baseline_still_deploys(self):
        # practice-12-sixth: the adapter left the first result pending, isuscope exited 1 and
        # first-bench stopped before baseline-ready and deploy.
        self.record_import()
        self.fake_survey_run('pending')
        result = self.run_first_bench(HYPOTHESIS='初期状態を記録する')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.lines()[-2:], ['isuscope survey-run --hypothesis 初期状態を記録する', 'deploy'])
        self.assertIn('scripts/benchmark-sample.py new-run', result.stdout)
        self.assertEqual(self.states(), ['started', 'checked', 'distributed', 'baseline-ready',
                                         'deployed', 'complete'])

    def test_a_failed_baseline_stops_before_deploy(self):
        self.record_import()
        self.fake_survey_run('failed')
        result = self.run_first_bench()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('deploy', self.lines())

    def test_a_change_committed_during_the_baseline_is_not_deployed_unmeasured(self):
        self.record_import()
        # The fake survey-run stands for the agent cherry-picking a lane commit meanwhile.
        (self.repo / 'bin/isuscope').write_text(
            self.FAKE_LIST + 'echo "isuscope $*" >> "$EVENTS"\n'
            'echo "fn main() { indexed() }" > webapp/go/main.go\n'
            'git -c user.name=t -c user.email=t@example.com commit -qam "Add an index"\n')
        result = self.run_first_bench()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('deploy', self.lines())
        self.assertEqual(self.states()[-2:], ['deploy-skipped', 'complete'])

    def test_deploys_first_when_the_distributed_state_cannot_be_used(self):
        cases = {
            'no import record': {},
            'changed since import': {'change': True},
            'app not running': {'env': {'FAIL': 'benchmark-prepare'}},
            'already deployed': {'deployed': True},
            'forced': {'env': {'FIRST_BENCH_MODE': 'deploy'}},
        }
        for label, case in cases.items():
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                if label != 'no import record':
                    self.record_import()
                if case.get('change'):
                    (self.repo / 'webapp/go/main.go').write_text('fn main() { faster() }\n')
                    subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=t', '-c', 'user.email=t@example.com',
                                    'commit', '-qam', 'Early fix'], check=True)
                if case.get('deployed'):
                    (self.repo / '.local/current-deploy-commit').write_text(self.head() + '\n')
                result = self.run_first_bench(**case.get('env', {}))
                self.assertEqual(result.returncode, 0, result.stderr)
                steps = [line for line in self.lines() if not line.startswith('benchmark-prepare')]
                self.assertEqual(steps, ['bench-check', 'deploy', 'isuscope survey-run --hypothesis 初期状態の負荷構造とベンチシナリオを記録する'])
                self.assertFalse((self.repo / '.local/current-import-commit').exists())

    def test_failed_check_never_deploys_or_benchmarks(self):
        result = self.run_first_bench(FAIL='bench-check')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.lines(), ['bench-check'])
        self.assertIn('first-bench.sh\tfailed\tbench-check', (self.repo / '.local/progress.log').read_text())
        self.events.unlink()
        self.assertNotEqual(self.run_first_bench(FAIL='deploy').returncode, 0)
        self.assertEqual(self.lines(), ['bench-check', 'deploy'])

    def test_uncommitted_import_or_a_lane_stops_before_anything(self):
        (self.repo / 'webapp/go/main.go').write_text('fn main() { changed() }\n')
        self.assertNotEqual(self.run_first_bench().returncode, 0)
        subprocess.run(['git', '-C', str(self.repo), 'checkout', '--', 'webapp'], check=True)
        self.assertNotEqual(self.run_first_bench(ISUCON_LANE='code-map').returncode, 0)
        self.assertEqual(self.lines(), [])


if __name__ == '__main__':
    unittest.main()
