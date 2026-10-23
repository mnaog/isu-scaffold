"""scripts/bench.sh (make bench): deploy, the benchmark and its brief in one background call; a failed
deploy starts no benchmark and puts the proposals the hook marked measuring back in the queue."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'}
FAKE_MAKE = '''#!/bin/sh
echo "make $*" >> "$FAKE_LOG"
if [ "$1" = deploy ]; then
  echo "changed items: app"
  if [ -n "$FAIL_DEPLOY" ]; then echo "validating failed on app1; rolled back" >&2; exit 1; fi
  echo "deploy complete: r1 (3s)"
fi
'''
FAKE_ISUSCOPE = '''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write('isuscope ' + json.dumps(sys.argv[1:], ensure_ascii=False) + '\\n')
if sys.argv[1] == 'run':
    print('✓ alp (app1, complete)')
    print('')
    print('run       abcd1234')
    print('result    PASS')
    print('score     123456')
    sys.exit(0)
if sys.argv[1] == 'brief':
    print(json.dumps({'run': sys.argv[2], 'summary': {'score': 123456}}))
'''


class BenchCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        (self.repo / 'scripts').mkdir(parents=True)
        for name in ('bench.sh', 'proposal.py', 'main-session.py', 'isuscope_sql.py', 'progress.py'):
            shutil.copy(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        (self.repo / 'config').mkdir()
        shutil.copy(ROOT / 'config/progress.json', self.repo / 'config/progress.json')
        bin_dir = Path(self.tmp.name) / 'bin'
        bin_dir.mkdir()
        for name, text in (('make', FAKE_MAKE), ('isuscope', FAKE_ISUSCOPE)):
            (bin_dir / name).write_text(text)
            (bin_dir / name).chmod(0o755)
        (self.repo / '.gitignore').write_text('.local/\n__pycache__/\n')
        (self.repo / 'app.txt').write_text('a\n')
        self.log = Path(self.tmp.name) / 'calls.log'
        self.env = {k: v for k, v in os.environ.items() if k not in ('CLAUDE_CODE_SESSION_ID', 'ISUCON_LANE')}
        self.env.update(GIT_ENV, PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}', FAKE_LOG=str(self.log),
                        PROPOSAL_AUTO_PREBUILD='0')
        self.git('init', '-q', '-b', 'main')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.run_script('main-session.py', 'claim', session='S-main')

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.repo, check=True, capture_output=True,
                              text=True, env=self.env).stdout.strip()

    def run_script(self, name, *args, session=None, cwd=None):
        env = dict(self.env, CLAUDE_CODE_SESSION_ID=session) if session else self.env
        result = subprocess.run([sys.executable, str(self.repo / 'scripts' / name), *args], cwd=cwd or self.repo,
                                capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def bench(self, *args, **env):
        return subprocess.run(['bash', str(self.repo / 'scripts/bench.sh'), *args], cwd=self.repo,
                              capture_output=True, text=True, env=dict(self.env, **env))

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def measure_a_proposal(self):
        """A proposal main merged and the hook marked measuring when the background call started."""
        worktree = Path(self.tmp.name) / 'repo-human'
        self.git('worktree', 'add', '-q', '-b', 'human', str(worktree))
        (worktree / 'b.txt').write_text('b\n')
        self.git('add', 'b.txt', cwd=worktree)
        self.git('commit', '-qm', 'Batch the inserts', cwd=worktree)
        self.run_script('proposal.py', 'submit', '--hypothesis', '座標をまとめてINSERTする', '--expect', 'x',
                        session='S-human', cwd=worktree)
        self.git('merge', '-q', '--no-ff', '-m', 'Take human', 'human')
        path = self.repo / '.local/proposals/human.json'
        data = json.loads(path.read_text())
        data.update(status='measuring', measuring_since='2026-10-23T12:00:00+0900')
        path.write_text(json.dumps(data))
        return path

    def test_deploys_runs_and_prints_the_result_and_that_runs_brief(self):
        result = self.bench('--repeat', '振れ幅: 1234abcd', '--hypothesis', '振れ幅を測る')
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(calls[0], 'make deploy')
        self.assertEqual(json.loads(calls[1].removeprefix('isuscope ')),
                         ['run', '--repeat', '振れ幅: 1234abcd', '--hypothesis', '振れ幅を測る'])
        self.assertEqual(json.loads(calls[2].removeprefix('isuscope ')), ['brief', 'abcd1234'])
        self.assertIn('deploy: deploy complete: r1 (3s)', result.stdout)
        self.assertIn('score     123456', result.stdout)
        self.assertIn('"run": "abcd1234"', result.stdout)
        self.assertTrue((self.repo / '.local/bench-last.out').exists())

    def test_without_a_hypothesis_it_measures_the_queue_s(self):
        self.measure_a_proposal()
        result = self.bench()
        self.assertEqual(result.returncode, 0, result.stderr)
        run = json.loads(self.calls()[1].removeprefix('isuscope '))
        self.assertEqual(run[:2], ['run', '--hypothesis'])
        self.assertIn('座標をまとめてINSERTする', run[2])

    def test_a_failed_deploy_starts_no_benchmark_and_puts_the_proposal_back(self):
        path = self.measure_a_proposal()
        result = self.bench(FAIL_DEPLOY='1')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), ['make deploy'])
        self.assertIn('the deploy failed, so the benchmark did not start', result.stdout)
        self.assertIn('validating failed on app1', result.stdout)
        self.assertEqual(json.loads(path.read_text())['status'], 'merged')

    def test_nothing_to_measure_and_no_hypothesis_is_refused_before_the_deploy(self):
        result = self.bench()
        self.assertEqual(result.returncode, 2)
        self.assertIn('pass --hypothesis', result.stderr)
        self.assertEqual(self.calls(), [])


class BenchCommandRecognitionTest(unittest.TestCase):
    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('bench_hook', ROOT / 'scripts/bench-hook.py')
        self.hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.hook)

    def payload(self, command, background=True):
        return {'tool_name': 'Bash', 'session_id': 'S', 'cwd': str(ROOT),
                'tool_input': {'command': command, 'run_in_background': background}}

    def test_the_hook_sees_make_bench_and_bench_sh_as_benchmarks(self):
        for command in ('make bench', 'scripts/bench.sh --hypothesis x', './scripts/bench.sh', 'bash scripts/bench.sh'):
            self.assertEqual(self.hook.starts_benchmark(ROOT, self.payload(command)), (True, True, False), command)
        # A comparison base given to make bench or bench.sh reaches the hook.
        for command in ('make bench BASE=856309ef', 'scripts/bench.sh --base 856309ef'):
            self.assertEqual(self.hook.starts_benchmark(ROOT, self.payload(command)), (True, True, True), command)
        for command in ('make bench-check', 'bash -n scripts/bench.sh', 'make build'):
            self.assertEqual(self.hook.starts_benchmark(ROOT, self.payload(command))[0], False, command)

    def test_lanes_may_not_run_it(self):
        payload = dict(self.payload('make bench'), hook_event_name='PreToolUse')
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/tool-guard.py')], input=json.dumps(payload),
                                capture_output=True, text=True, env=dict(os.environ, ISUCON_LANE='indexes'))
        decision = json.loads(result.stdout)['hookSpecificOutput']['permissionDecision']
        self.assertEqual(decision, 'deny', result.stdout)


if __name__ == '__main__':
    unittest.main()
