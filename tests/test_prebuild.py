"""scripts/prebuild.py: during a benchmark, the trees main may deploy next are built and tested into the
shared cache, so main's own merge, revert and deploy afterwards find them there; a waiting proposal that
conflicts with main's branch is reported before main merges it."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tests'))
import isuscope_stub  # noqa: E402,F401
from test_local_build import FAKE_GO  # noqa: E402
import isu_hook  # noqa: E402
GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'}
SCRIPTS = ('main-session.py', 'proposal.py', 'isuscope_sql.py', 'progress.py', 'prebuild.py', 'local-build.py')


class PrebuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.repo = base / 'repo'
        for folder in ('scripts', 'config', 'webapp/go', 'bin'):
            (self.repo / folder).mkdir(parents=True)
        for name in SCRIPTS:
            shutil.copy(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        shutil.copy(ROOT / 'config/progress.json', self.repo / 'config/progress.json')
        (self.repo / 'bin/go').write_text(FAKE_GO)
        (self.repo / 'bin/go').chmod(0o755)
        (self.repo / '.gitignore').write_text('.local/\nbin/\n__pycache__/\nisuscope-data/\n')
        (self.repo / 'webapp/go/go.mod').write_text('module example.com/app\n\ngo 1.23\n')
        (self.repo / 'webapp/go/main.go').write_text('package main\n\nfunc main() {}\n')
        (self.repo / 'webapp/go/store_test.go').write_text('package main\n')
        (self.repo / 'config/sync.json').write_text(json.dumps({
            'items': [{'name': 'app', 'type': 'directory', 'node_group': 'application', 'local': 'webapp/go',
                       'remote': '/home/isucon/webapp/go', 'owner': 'isucon', 'owner_group': 'isucon'}],
            'local_builds': [{'item': 'app', 'target': 'linux/amd64', 'binary': 'app'}]}))
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ('CLAUDE_CODE_SESSION_ID', 'CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'ISUCON_LANE')}
        self.env.update(GIT_ENV, PATH=str(self.repo / 'bin') + os.pathsep + os.environ['PATH'],
                        FAKE_ROOT=str(self.repo / '.local'))
        (self.repo / '.local').mkdir()
        self.git('init', '-q', '-b', 'main')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.human = base / 'human-repo'
        self.git('worktree', 'add', '-q', '-b', 'human', str(self.human))
        self.script('main-session.py', 'claim', session='S-main')

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.repo, check=True, capture_output=True,
                              text=True, env=self.env).stdout.strip()

    def script(self, name, *args, cwd=None, session=None, env=None):
        environment = dict(self.env, **(env or {}))
        if session:
            environment['CLAUDE_CODE_SESSION_ID'] = session
        result = subprocess.run([sys.executable, str(self.repo / 'scripts' / name), *args], cwd=cwd or self.repo,
                                capture_output=True, text=True, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def submit(self, branch, path, text, prebuild=False):
        self.git('switch', '-q', '-c', branch, 'main', cwd=self.human)
        (self.human / path).write_text(text)
        self.git('add', path, cwd=self.human)
        self.git('commit', '-qm', f'Change {path}', cwd=self.human)
        # The cases below run the prebuild themselves; a submit would start one in the background.
        self.script('proposal.py', 'submit', '--hypothesis', f'{branch} is faster', '--expect', 'x',
                    cwd=self.human, session='S-human', env={} if prebuild else {'PROPOSAL_AUTO_PREBUILD': '0'})
        self.git('switch', '-q', '--detach', cwd=self.human)

    def start_benchmark(self):
        payload = {'tool_name': 'Bash', 'session_id': 'S-main', 'cwd': str(self.repo),
                   'tool_input': {'command': 'isuscope run --hypothesis x', 'run_in_background': True}}
        result = subprocess.run(isu_hook.command('post'), input=json.dumps(payload), text=True,
                                capture_output=True,
                                env=dict(self.env, TOOL_GUARD_ROOT=str(self.repo), PROPOSAL_AUTO_SYNC='0'))
        self.assertEqual(result.returncode, 0, result.stderr)

    def go_builds(self):
        log = self.repo / '.local/go.log'
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [call for call in calls if call['args'][0] == 'build']

    def deploy_build(self):
        """What `make deploy` does before contacting a node: build (and test) HEAD's tree."""
        return self.script('local-build.py', 'build', '--manifest', 'config/sync.json').stderr

    def test_the_tree_main_merges_after_the_benchmark_was_built_and_tested_during_it(self):
        self.submit('improve-a', 'webapp/go/a.go', 'package main\n// a\n')
        self.git('merge', '-q', '--no-ff', '-m', 'Merge improve-a', 'improve-a')
        self.deploy_build()
        self.start_benchmark()  # improve-a is measured now
        self.submit('improve-b', 'webapp/go/b.go', 'package main\n// b\n')
        builds_before = len(self.go_builds())

        output = self.script('prebuild.py').stdout
        record = json.loads((self.repo / '.local/prebuild.json').read_text())
        variants = {result['variant']: result for result in record['results']}
        self.assertEqual(list(variants), ['keep+improve-b', 'drop+improve-b', 'keep', 'drop'], output)
        self.assertTrue(all(result['status'] == 'ok' for result in record['results']), record)
        self.assertTrue(variants['keep']['cached'], 'the measured tree itself was already built')
        self.assertEqual(len(self.go_builds()), builds_before + 3)
        self.assertIn('4 of 4 trees built and tested', output)
        # main's worktree, branch and proposals are untouched.
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(self.git('log', '-1', '--format=%s'), 'Merge improve-a')

        # After the analysis main rejects improve-a and measures improve-b: both builds are found.
        merge = self.git('rev-parse', 'HEAD')
        self.git('revert', '--no-edit', '-m', '1', merge)
        self.assertIn('local build: cached', self.deploy_build())
        self.git('merge', '-q', '--no-ff', '-m', 'Merge improve-b', 'improve-b')
        self.assertEqual(self.git('rev-parse', 'HEAD^{tree}:webapp/go'),
                         self.git('rev-parse', f"{variants['drop+improve-b']['tree']}:webapp/go"))
        self.assertIn('local build: cached', self.deploy_build())
        self.assertEqual(len(self.go_builds()), builds_before + 3, 'deploy built nothing again')

    def test_a_waiting_proposal_that_conflicts_is_reported_before_main_merges_it(self):
        self.submit('improve-c', 'webapp/go/main.go', 'package main\n\nfunc main() { println("c") }\n')
        # main's branch moves on and touches the same line.
        (self.repo / 'webapp/go/main.go').write_text('package main\n\nfunc main() { println("main") }\n')
        self.git('commit', '-qam', 'Change main on main')
        self.script('prebuild.py')
        record = json.loads((self.repo / '.local/prebuild.json').read_text())
        statuses = {result['variant']: result['status'] for result in record['results']}
        self.assertEqual(statuses, {'keep+improve-c': 'conflict', 'keep': 'ok'})
        progress = (self.repo / '.local/progress.log').read_text()
        self.assertIn('prebuild\tfailed\timprove-c', progress)
        self.assertIn('conflict: webapp/go/main.go', progress)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_a_proposal_whose_tests_fail_on_main_is_reported(self):
        self.submit('improve-d', 'webapp/go/d.go', 'package main\n// d\n')
        self.script('prebuild.py', env={'FAIL_TEST': '1'})
        record = json.loads((self.repo / '.local/prebuild.json').read_text())
        self.assertEqual({result['variant']: result['status'] for result in record['results']},
                         {'keep+improve-d': 'test-failed', 'keep': 'test-failed'})
        self.assertIn('improve-d', (self.repo / '.local/progress.log').read_text())

    def test_the_benchmark_hook_starts_it_after_the_sync(self):
        self.submit('improve-e', 'webapp/go/e.go', 'package main\n// e\n')
        payload = {'tool_name': 'Bash', 'session_id': 'S-main', 'cwd': str(self.repo),
                   'tool_input': {'command': 'isuscope run --hypothesis x', 'run_in_background': True}}
        hook = self.repo / 'scripts/hook.sh'
        for name in ('bench-hook.py', 'isuscope-prune.py', 'hook.sh'):
            shutil.copy(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        result = subprocess.run([str(hook), 'post'], input=json.dumps(payload), text=True, capture_output=True,
                                env=dict(self.env, TOOL_GUARD_ROOT=str(self.repo), ISU_HOOK_BIN=str(isu_hook.build())))
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.wait_for_record()
        self.assertTrue((self.repo / '.local/proposal-sync.log').exists(), 'the sync ran first')
        variants = [result['variant'] for result in record['results']]
        self.assertEqual(variants, ['keep+improve-e', 'keep'])

    def wait_for_record(self, proposal=None):
        record = self.repo / '.local/prebuild.json'
        for _ in range(300):
            if record.exists():
                data = json.loads(record.read_text())
                if proposal is None or data.get('proposal') == proposal:
                    return data
            time.sleep(0.1)
        log = self.repo / '.local/prebuild.log'
        self.fail(log.read_text() if log.exists() else 'no prebuild log')

    def test_a_submit_prebuilds_that_proposal_on_the_measured_branch_kept_and_dropped(self):
        self.submit('improve-a', 'webapp/go/a.go', 'package main\n// a\n')
        self.git('merge', '-q', '--no-ff', '-m', 'Merge improve-a', 'improve-a')
        self.start_benchmark()  # improve-a is measured now; the next one arrives during the benchmark
        self.submit('improve-f', 'webapp/go/f.go', 'package main\n// f\n', prebuild=True)
        record = self.wait_for_record('improve-f')
        self.assertEqual([result['variant'] for result in record['results']], ['keep+improve-f', 'drop+improve-f'])
        self.assertTrue(all(result['status'] == 'ok' for result in record['results']), record)
        builds = len(self.go_builds())
        self.git('merge', '-q', '--no-ff', '-m', 'Merge improve-f', 'improve-f')
        self.assertIn('local build: cached', self.deploy_build())
        self.assertEqual(len(self.go_builds()), builds)

    def test_the_record_keeps_the_earlier_runs(self):
        self.submit('improve-g', 'webapp/go/g.go', 'package main\n// g\n')
        self.script('prebuild.py')
        self.script('prebuild.py', '--proposal', 'improve-g')
        record = json.loads((self.repo / '.local/prebuild.json').read_text())
        self.assertEqual(record['proposal'], 'improve-g')
        self.assertEqual(len(record['history']), 1)
        self.assertIsNone(record['history'][0]['proposal'])
        self.assertNotIn('history', record['history'][0])

    def test_it_can_be_turned_off(self):
        self.script('prebuild.py', env={'PROPOSAL_AUTO_PREBUILD': '0'})
        self.assertFalse((self.repo / '.local/prebuild.json').exists())


if __name__ == '__main__':
    unittest.main()
