import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class FirstBenchTests(unittest.TestCase):
    """make first-bench chains the checked steps and stops at the first failure."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        for folder in ('scripts', 'bin', 'webapp/rust', 'config'):
            (self.repo / folder).mkdir(parents=True)
        for name in ('first-bench.sh', 'stage-lib.sh', 'progress.py'):
            shutil.copy(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        for name in ('phase1-check.sh', 'deploy.sh'):
            step = name.removesuffix('.sh')
            (self.repo / 'scripts' / name).write_text(
                f'#!/bin/sh\necho {step} >> "$EVENTS"\n[ "$FAIL" = {step} ] && exit 1\nexit 0\n')
        (self.repo / 'bin/isuscope').write_text('#!/bin/sh\necho "isuscope $*" >> "$EVENTS"\n')
        for path in [*(self.repo / 'scripts').glob('*.sh'), self.repo / 'bin/isuscope']:
            path.chmod(0o755)
        (self.repo / 'webapp/rust/main.rs').write_text('fn main() {}\n')
        (self.repo / '.gitignore').write_text('.local/\nbin/\nevents\n')
        for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-qm', 'fixture']):
            subprocess.run(['git', '-C', str(self.repo), *args], check=True)
        self.events = self.repo / 'events'

    def tearDown(self):
        self.tmp.cleanup()

    def run_first_bench(self, **env):
        environment = {k: v for k, v in os.environ.items() if k not in ('ISUCON_LANE', 'PROGRESS_LOG', 'FAIL')}
        environment.update(PATH=f'{self.repo / "bin"}{os.pathsep}{os.environ["PATH"]}', EVENTS=str(self.events), **env)
        return subprocess.run(['bash', 'scripts/first-bench.sh'], cwd=self.repo, env=environment,
                              capture_output=True, text=True)

    def lines(self):
        return self.events.read_text().splitlines() if self.events.exists() else []

    def test_check_deploy_then_survey_run(self):
        result = self.run_first_bench(HYPOTHESIS='初期状態を記録する')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.lines(), ['phase1-check', 'deploy', 'isuscope survey-run --hypothesis 初期状態を記録する'])
        states = [line.split('\t')[2] for line in (self.repo / '.local/progress.log').read_text().splitlines()]
        self.assertEqual(states, ['started', 'checked', 'deployed', 'complete'])

    def test_failed_check_never_deploys_or_benchmarks(self):
        result = self.run_first_bench(FAIL='phase1-check')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.lines(), ['phase1-check'])
        self.assertIn('first-bench.sh\tfailed\tphase1-check', (self.repo / '.local/progress.log').read_text())
        self.events.unlink()
        self.assertNotEqual(self.run_first_bench(FAIL='deploy').returncode, 0)
        self.assertEqual(self.lines(), ['phase1-check', 'deploy'])

    def test_uncommitted_import_or_a_lane_stops_before_anything(self):
        (self.repo / 'webapp/rust/main.rs').write_text('fn main() { changed() }\n')
        self.assertNotEqual(self.run_first_bench().returncode, 0)
        subprocess.run(['git', '-C', str(self.repo), 'checkout', '--', 'webapp'], check=True)
        self.assertNotEqual(self.run_first_bench(ISUCON_LANE='code-map').returncode, 0)
        self.assertEqual(self.lines(), [])


if __name__ == '__main__':
    unittest.main()
