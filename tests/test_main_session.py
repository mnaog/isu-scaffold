import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / 'scripts/tool-guard.py'
HOOK = ROOT / 'scripts/bench-hook.py'
GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'}


class MainSessionTest(unittest.TestCase):
    """/main declares the session that deploys and benchmarks; worktrees hand it proposals."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / 'repo'
        (self.repo / 'scripts').mkdir(parents=True)
        for name in ('main-session.py', 'proposal.py'):
            shutil.copy(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        (self.repo / 'app.txt').write_text('a\nb\nc\n')
        (self.repo / '.gitignore').write_text('.local/\n')
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ('CLAUDE_CODE_SESSION_ID', 'CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'ISUCON_LANE')}
        self.env.update(GIT_ENV)
        self.git('init', '-q', '-b', 'main')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.worktree = Path(self.tmp.name) / 'repo-idea'
        self.git('worktree', 'add', '-q', '-b', 'idea/cache', str(self.worktree))

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.repo, check=True, capture_output=True,
                              text=True, env=self.env).stdout

    def script(self, name, *args, cwd=None, session=None, check=True):
        env = dict(self.env, CLAUDE_CODE_SESSION_ID=session) if session else self.env
        result = subprocess.run(['python3', str(self.repo / 'scripts' / name), *args], cwd=cwd or self.repo,
                                capture_output=True, text=True, env=env)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def commit_in_worktree(self, path='app.txt', text='a\nB\nc\n', message='Change b'):
        (self.worktree / path).write_text(text)
        self.git('add', path, cwd=self.worktree)
        self.git('commit', '-qm', message, cwd=self.worktree)

    def submit(self, session='S-worker'):
        return self.script('proposal.py', 'submit', '--hypothesis', 'bを大文字にすると速い',
                           '--expect', 'GET /b のp99が下がる', '--verified', 'cargo test',
                           cwd=self.worktree, session=session, check=False)

    def deliver(self, session):
        """What bench-hook --post hands this session when it starts a benchmark in the background."""
        payload = {'tool_name': 'Bash', 'session_id': session, 'cwd': str(self.repo),
                   'tool_input': {'command': 'isuscope run --hypothesis x', 'run_in_background': True}}
        env = dict(self.env, TOOL_GUARD_ROOT=str(self.repo))
        result = subprocess.run(['python3', str(HOOK), '--post'], input=json.dumps(payload), text=True,
                                capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        return json.loads(result.stdout)['hookSpecificOutput']['additionalContext'] if result.stdout.strip() else ''

    def guard(self, command, session, env=None):
        payload = {'tool_name': 'Bash', 'tool_input': {'command': command}, 'session_id': session}
        env = dict(self.env, TOOL_GUARD_ROOT=str(self.worktree), **(env or {}))
        result = subprocess.run(['python3', str(GUARD)], input=json.dumps(payload), text=True,
                                capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['hookSpecificOutput']['permissionDecisionReason'] if result.stdout.strip() else None

    def test_main_is_claimed_in_the_root_and_taken_over_by_claiming_again(self):
        refused = self.script('main-session.py', 'claim', cwd=self.worktree, session='S-worker', check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('worktree', refused.stderr)
        self.assertIn('S-main', self.script('main-session.py', 'claim', session='S-main').stdout)
        declared = json.loads((self.repo / '.local/main-session.json').read_text())
        self.assertEqual((declared['agent'], declared['session'], declared['branch']), ('claude', 'S-main', 'main'))
        taken = self.script('main-session.py', 'claim', session='S-next').stdout
        self.assertIn('引き継いだ', taken)
        declared = json.loads((self.repo / '.local/main-session.json').read_text())
        self.assertEqual(declared['previous']['session'], 'S-main')
        refused = self.script('main-session.py', 'release', session='S-main', check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.script('main-session.py', 'release', session='S-next')
        self.assertFalse((self.repo / '.local/main-session.json').exists())

    def test_only_main_deploys_and_benchmarks_while_a_main_is_declared(self):
        main_only = ['make deploy', 'make first-bench', 'isuscope run --hypothesis x', 'isuscope analyze abc supported',
                     'python3 scripts/lane.py start full-memory', 'bash scripts/deploy.sh', 'isuscope lock -- make build',
                     'cat .local/operation.lock', 'make scout-start', 'python3 scripts/tare.py',
                     'make final-reboot', 'make final-db-late', 'python3 scripts/final-verification.py reboot --yes']
        free = ['make build', 'isuscope brief latest', 'isuscope states timeline latest', 'git status',
                'python3 scripts/lane.py status', 'make local-up', 'python3 scripts/proposal.py list',
                'make final-check', 'python3 scripts/final-verification.py check']
        # No main declared: nothing is refused, as before /main.
        self.assertIsNone(self.guard('make deploy', 'S-worker'))
        self.script('main-session.py', 'claim', session='S-main')
        for command in main_only:
            with self.subTest(command=command):
                self.assertIn('mainのセッション', self.guard(command, 'S-worker') or '')
                self.assertIsNone(self.guard(command, 'S-main'))
        for command in free:
            with self.subTest(command=command):
                self.assertIsNone(self.guard(command, 'S-worker'))
        self.assertIsNone(self.guard('make deploy', ''))  # no session id: cannot tell, so not checked
        # The portal session builds the benchmark connection: the adapter's checks never start a benchmark.
        for command in ['.isuscope/benchmark.sh --check', 'bash .isuscope/benchmark.sh --probe',
                        'python3 scripts/benchmark-contract.py --seal']:
            with self.subTest(command=command):
                self.assertIsNone(self.guard(command, 'S-worker'))
        for command in ['.isuscope/benchmark.sh', 'bash .isuscope/benchmark.sh --extract out.txt',
                        '.isuscope/benchmark.sh --probe --check && .isuscope/benchmark.sh']:
            with self.subTest(command=command):
                self.assertIn('mainのセッション', self.guard(command, 'S-worker') or '')
        # Codex: the thread id main claimed with, even when the hook payload carries another id.
        self.assertIsNone(self.guard('make deploy', 'hook-id', env={'CODEX_THREAD_ID': 'S-main'}))

    def test_a_proposal_reaches_main_once_per_commit_and_its_decision_comes_back(self):
        self.assertIn('commit', self.submit().stderr)  # nothing beyond main yet
        (self.worktree / 'app.txt').write_text('dirty\n')
        self.assertIn('commitしていない', self.submit().stderr)
        self.git('checkout', '--', 'app.txt', cwd=self.worktree)
        self.commit_in_worktree()
        self.assertEqual(self.submit().returncode, 0)
        refused = self.script('proposal.py', 'submit', '--hypothesis', 'x', '--expect', 'y', '--verified', 'z',
                              session='S-main', check=False)
        self.assertIn('worktree', refused.stderr)  # the root is main's
        self.script('main-session.py', 'claim', session='S-main')
        self.assertEqual(self.deliver('S-worker'), '')  # not main: nothing handed over
        context = self.deliver('S-main')
        self.assertIn('### idea/cache', context)
        self.assertIn('GET /b のp99が下がる', context)
        self.assertIn('app.txt', context)
        self.assertIn('git merge --no-ff idea/cache', context)
        self.assertNotIn('idea/cache', self.deliver('S-main'))  # once per commit
        badge = json.loads(self.script('main-session.py', 'badge', '--session', 'S-worker', cwd=self.worktree).stdout)
        self.assertEqual((badge['role'], badge['branch'], badge['proposal']['status']), ('other', 'idea/cache', 'delivered'))
        badge = json.loads(self.script('main-session.py', 'badge', '--session', 'S-main').stdout)
        self.assertEqual((badge['role'], badge['waiting']), ('main', 1))
        self.commit_in_worktree('more.txt', 'x\n', 'Add more')
        self.assertEqual(self.submit().returncode, 0)
        self.assertIn('### idea/cache', self.deliver('S-main'))  # a new commit is handed over again
        self.script('proposal.py', 'decide', 'idea/cache', 'rejected', '--run', 'ab12cd34', '--note', '効かない',
                    session='S-main')
        shown = self.script('proposal.py', 'show', cwd=self.worktree).stdout
        self.assertIn('rejected', shown)
        self.assertIn('ab12cd34', shown)

    def test_a_conflicting_proposal_waits_for_its_author_and_a_merged_one_closes(self):
        self.commit_in_worktree()
        self.assertEqual(self.submit().returncode, 0)
        (self.repo / 'app.txt').write_text('a\nbee\nc\n')
        self.git('commit', '-qam', 'Main changes b too')
        self.script('main-session.py', 'claim', session='S-main')
        context = self.deliver('S-main')
        self.assertNotIn('### idea/cache', context)
        self.assertIn('衝突', context)
        shown = self.script('proposal.py', 'show', cwd=self.worktree).stdout
        self.assertIn('needs-sync', shown)
        self.assertIn('app.txt', shown)
        # The author merges main, resolves, and submits again.
        subprocess.run(['git', 'merge', '-q', 'main'], cwd=self.worktree, capture_output=True, env=self.env)
        (self.worktree / 'app.txt').write_text('a\nBEE\nc\n')
        self.git('commit', '-qam', 'Merge main', cwd=self.worktree)
        self.assertEqual(self.submit().returncode, 0)
        self.assertIn('### idea/cache', self.deliver('S-main'))
        self.git('merge', '-q', '--no-ff', '-m', 'Take idea/cache', 'idea/cache')
        self.assertIn('merged', self.script('proposal.py', 'list', '--all').stdout)


if __name__ == '__main__':
    unittest.main()
