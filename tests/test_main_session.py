import fcntl
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
        for name in ('main-session.py', 'proposal.py', 'progress.py', 'runs.sh'):
            shutil.copy2(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        (self.repo / 'scripts' / 'board').mkdir()
        shutil.copy(ROOT / 'scripts' / 'board' / 'activity.py', self.repo / 'scripts' / 'board' / 'activity.py')
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
        self.branch_of = {self.worktree: 'idea/cache'}

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
        self.back_to_branch(self.worktree)
        (self.worktree / path).write_text(text)
        self.git('add', path, cwd=self.worktree)
        self.git('commit', '-qm', message, cwd=self.worktree)

    def submit(self, session='S-worker'):
        self.remember_branch(self.worktree)
        return self.script('proposal.py', 'submit', '--hypothesis', 'bを大文字にすると速い',
                           '--expect', 'GET /b のp99が下がる', '--verified', 'cargo test', '--based-on', '1a2b3c4d',
                           cwd=self.worktree, session=session, check=False)

    def progress(self):
        path = self.repo / '.local/progress.log'
        return [line.split('\t')[1:] for line in path.read_text().splitlines()] if path.exists() else []

    def deliver(self, session):
        """What bench-hook --post hands this session when it starts a benchmark in the background."""
        payload = {'tool_name': 'Bash', 'session_id': session, 'cwd': str(self.repo),
                   'tool_input': {'command': 'isuscope run --hypothesis x', 'run_in_background': True}}
        env = dict(self.env, TOOL_GUARD_ROOT=str(self.repo), PROPOSAL_AUTO_SYNC='0')
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

    def test_the_badge_shows_a_benchmark_only_while_it_holds_the_operation_lock(self):
        lock = self.repo / '.local' / 'operation.lock'
        lock.mkdir(parents=True)
        (lock / 'owner').write_text('pid=1\nstarted_at=2026-10-16T08:00:00+0900\noperation=isuscope run\n')
        badge = json.loads(self.script('main-session.py', 'badge', '--session', 'S').stdout)
        self.assertIsNone(badge['benchmark'])
        with (lock / 'lock').open('wb') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            badge = json.loads(self.script('main-session.py', 'badge', '--session', 'S', cwd=self.worktree).stdout)
        self.assertEqual((badge['benchmark']['kind'], badge['benchmark']['started_at']), ('run', '2026-10-16T08:00:00+0900'))
        self.assertGreater(badge['benchmark']['elapsed_seconds'], 0)
        (lock / 'owner').write_text('pid=1\nstarted_at=2026-10-16T08:00:00+0900\noperation=deploy.sh\n')
        with (lock / 'lock').open('wb') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            badge = json.loads(self.script('main-session.py', 'badge', '--session', 'S').stdout)
        self.assertIsNone(badge['benchmark'])

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
                     'cat .local/operation.lock', 'make tare', 'python3 scripts/tare.py',
                     'make final-reboot', 'make final-db-late', 'python3 scripts/final-verification.py reboot --yes',
                     'isuscope change decide c1 accepted', 'python3 scripts/proposal.py decide idea/cache accepted',
                     'python3 scripts/proposal.py pending']
        free = ['make build', 'isuscope brief latest', 'isuscope states timeline latest', 'git status',
                'python3 scripts/lane.py status', 'make local-up', 'python3 scripts/proposal.py list',
                'make final-check', 'python3 scripts/final-verification.py check',
                # improve reads runs and submits; the decision stays with main.
                'isuscope series latest --metric http.requests', 'isuscope change list', 'isuscope change show c1',
                'scripts/runs.sh brief latest', 'python3 scripts/proposal.py submit --hypothesis x --expect y --verified z',
                'python3 scripts/progress.py emit improve working "improve-cache: GET /b"']
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
        self.assertIn('根拠のrun: 1a2b3c4d', context)
        self.assertIn('app.txt', context)
        self.assertIn('git merge --no-ff idea/cache', context)
        self.assertNotIn('idea/cache', self.deliver('S-main'))  # once per commit
        badge = json.loads(self.script('main-session.py', 'badge', '--session', 'S-worker', cwd=self.worktree).stdout)
        self.assertEqual((badge['role'], badge['proposal']['status']), ('other', 'delivered'))  # found after submit left the branch
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
        self.assertIn('根拠のrun: 1a2b3c4d', shown)
        # Both ends follow .local/progress.log in the root: the worktree's submit and main's decision.
        lines = self.progress()
        self.assertEqual([line[:2] for line in lines], [['proposal', 'submitted']] * 2 + [['proposal', 'rejected']])
        self.assertTrue(lines[0][2].startswith('idea/cache '), lines)
        self.assertIn('ab12cd34', lines[-1][2])

    def test_a_worktree_says_what_it_builds_until_it_submits(self):
        refused = self.script('proposal.py', 'working', '索引', session='S-main', check=False)
        self.assertNotEqual(refused.returncode, 0)  # main does not implement
        self.script('proposal.py', 'working', 'app.txtのbを大文字にする（DBの読み取りが最大の制約）',
                    cwd=self.worktree, session='S-worker')
        queue = json.loads(subprocess.run(['python3', '-c', 'import importlib.util,json,sys;'
                                           's=importlib.util.spec_from_file_location("p",sys.argv[1]);m=importlib.util.module_from_spec(s);'
                                           's.loader.exec_module(m);print(json.dumps(m.queue(sys.argv[2])))',
                                           str(self.repo / 'scripts/proposal.py'), str(self.repo)],
                                          capture_output=True, text=True, check=True).stdout)
        self.assertEqual([(w['name'], w['role'], w['branch']) for w in queue['working']], [('idea', 'worktree', 'idea/cache')])
        self.assertIn('DBの読み取り', queue['working'][0]['what'])
        self.assertIn('worktree\tworking\tidea idea/cache', (self.repo / '.local/progress.log').read_text())
        self.commit_in_worktree()
        self.assertNotIn('宣言していなかったようです。', self.submit().stdout)
        self.assertFalse((self.repo / '.local/proposal-state/working/idea.json').exists())
        # The run's hypothesis is the merged proposal's own.
        self.assertNotEqual(self.script('proposal.py', 'hypothesis', check=False).returncode, 0)
        self.git('merge', '--no-ff', '-q', 'idea/cache', '-m', 'Merge idea/cache')
        out = self.script('proposal.py', 'hypothesis').stdout.strip()
        self.assertTrue(out.startswith('idea/cache: bを大文字にすると速い'), out)
        self.assertIn('期待: ', out)
        # Submitting without having said so first is reminded to say so next time.
        self.commit_in_worktree(text='a\nB\nC\n', message='Change c')
        self.assertIn('宣言していなかったようです。', self.submit().stdout)
        self.script('proposal.py', 'working', 'もう1つ', cwd=self.worktree, session='S-worker')
        self.script('proposal.py', 'working', '--done', cwd=self.worktree, session='S-worker')
        self.assertFalse((self.repo / '.local/proposal-state/working/idea.json').exists())

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
        # The author comes back to the branch, merges main, resolves, and submits again.
        self.back_to_branch(self.worktree)
        subprocess.run(['git', 'merge', '-q', 'main'], cwd=self.worktree, capture_output=True, env=self.env)
        (self.worktree / 'app.txt').write_text('a\nBEE\nc\n')
        self.git('commit', '-qam', 'Merge main', cwd=self.worktree)
        self.assertEqual(self.submit().returncode, 0)
        self.assertIn('### idea/cache', self.deliver('S-main'))
        self.git('merge', '-q', '--no-ff', '-m', 'Take idea/cache', 'idea/cache')
        self.assertIn('merged', self.script('proposal.py', 'list', '--all').stdout)

    def test_main_pulls_a_proposal_while_no_benchmark_runs(self):
        self.commit_in_worktree()
        self.assertEqual(self.submit().returncode, 0)
        self.script('main-session.py', 'claim', session='S-main')
        refused = self.script('proposal.py', 'pending', session='S-worker', check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('main', refused.stderr)
        pulled = self.script('proposal.py', 'pending', session='S-main').stdout
        self.assertIn('### idea/cache', pulled)
        self.assertIn('どう組み合わせて測るか', pulled)
        # Pulled once per commit, like the hook: the next benchmark does not hand it again.
        self.assertIn('新しい投稿は無い', self.script('proposal.py', 'pending', session='S-main').stdout)
        self.assertNotIn('idea/cache', self.deliver('S-main'))

    def test_runs_are_read_in_the_root_from_a_worktree(self):
        bin_dir = Path(self.tmp.name) / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'isuscope').write_text('#!/bin/sh\necho "$PWD $*"\n')
        (bin_dir / 'isuscope').chmod(0o755)
        env = dict(self.env, PATH=f'{bin_dir}:{self.env["PATH"]}')
        shutil.copy2(self.repo / 'scripts/runs.sh', self.worktree / 'scripts/runs.sh')

        def runs(*args):
            return subprocess.run([str(self.worktree / 'scripts/runs.sh'), *args], cwd=self.worktree,
                                  capture_output=True, text=True, env=env)
        missing = runs('brief', 'latest')
        self.assertEqual(missing.returncode, 1)
        self.assertIn('config.toml', missing.stderr)
        (self.repo / '.isuscope').mkdir()
        (self.repo / '.isuscope/config.toml').write_text('')
        read = runs('brief', 'latest')
        self.assertEqual(read.returncode, 0, read.stderr)
        self.assertEqual(read.stdout.strip(), f'{self.repo.resolve()} brief latest')
        self.assertEqual(runs('change', 'show', 'c1').returncode, 0)
        for args in (['run', '--hypothesis', 'x'], ['analyze', 'r', 'supported'], ['change', 'decide', 'c1', 'accepted'], []):
            with self.subTest(args=args):
                refused = runs(*args)
                self.assertEqual(refused.returncode, 2)
                self.assertIn('reads runs only', refused.stderr)

    def add_worktree(self, name, branch=None):
        path = Path(self.tmp.name) / f'repo-{name}'
        self.git('worktree', 'add', '-q', '-b', branch or name, str(path))
        self.branch_of[path] = branch or name
        return path

    def remember_branch(self, path):
        branch = subprocess.run(['git', 'symbolic-ref', '-q', '--short', 'HEAD'], cwd=path, capture_output=True, text=True)
        if branch.returncode == 0:
            self.branch_of[path] = branch.stdout.strip()

    def back_to_branch(self, path):
        """submit left the branch; the author comes back to it to commit again."""
        if subprocess.run(['git', 'symbolic-ref', '-q', 'HEAD'], cwd=path, capture_output=True).returncode:
            self.git('switch', '-q', self.branch_of[path], cwd=path)

    def change(self, path, file, text, message):
        self.back_to_branch(path)
        (path / file).write_text(text)
        self.git('add', file, cwd=path)
        self.git('commit', '-qm', message, cwd=path)

    def submit_from(self, path, session, *extra):
        self.remember_branch(path)
        return self.script('proposal.py', 'submit', '--hypothesis', f'{path.name}の仮説', '--expect', 'x',
                           '--verified', 'y', *extra, cwd=path, session=session, check=False)

    def status(self, branch):
        return json.loads((self.repo / '.local/proposals' / (branch.replace('/', '__') + '.json')).read_text())['status']

    def test_improve_adds_only_while_at_most_one_waits_and_waits_for_room(self):
        self.script('main-session.py', 'claim', session='S-main')
        improve = self.add_worktree('improve')
        refused = self.script('proposal.py', 'improve', session='S-improve', check=False)
        self.assertNotEqual(refused.returncode, 0)  # the root is main's
        self.assertIn('improve', self.script('proposal.py', 'improve', cwd=improve, session='S-improve').stdout)
        self.assertIn(['improve', 'started'], [line[:2] for line in self.progress()])
        human = self.add_worktree('human')
        for number in (1, 2):
            self.git('switch', '-q', '-c', f'human-{number}', 'main', cwd=human)
            self.change(human, f'h{number}.txt', 'h\n', f'Human {number}')
            self.assertEqual(self.submit_from(human, 'S-human').returncode, 0)
        # Two wait in the whole queue: improve may not add, people may.
        self.git('switch', '-q', '-c', 'improve-a', 'main', cwd=improve)
        self.change(improve, 'a.txt', 'a\n', 'Improve a')
        full = self.submit_from(improve, 'S-improve')
        self.assertEqual(full.returncode, 3, full.stderr)
        self.assertIn('wait', full.stderr)
        self.git('switch', '-q', '-c', 'human-3', 'main', cwd=human)
        self.change(human, 'h3.txt', 'h\n', 'Human 3')
        self.assertEqual(self.submit_from(human, 'S-human').returncode, 0)
        timed_out = self.script('proposal.py', 'wait', '--timeout', '0', cwd=improve, session='S-improve', check=False)
        self.assertEqual(timed_out.returncode, 2)
        # main sends two back without measuring them; a held one does not count either.
        self.assertIn('ベンチにかけずに', self.script('proposal.py', 'decide', 'human-1', 'rejected', '--note', '効かない',
                                                 session='S-main').stdout)
        self.script('proposal.py', 'hold', 'human-2', '--until', 'DBが最大の制約になったら', session='S-main')
        self.assertEqual(self.status('human-2'), 'held')
        self.assertIn('1件', self.script('proposal.py', 'wait', '--timeout', '0', cwd=improve, session='S-improve').stdout)
        self.assertEqual(self.submit_from(improve, 'S-improve').returncode, 0)
        submitted = json.loads((self.repo / '.local/proposals/improve-a.json').read_text())
        self.assertEqual(submitted['author']['role'], 'improve')
        # Now two wait again; replacing improve's own waiting proposal adds nothing, so it goes through.
        self.change(improve, 'a.txt', 'a2\n', 'Improve a again')
        self.assertEqual(self.submit_from(improve, 'S-improve').returncode, 0)
        self.script('proposal.py', 'unhold', 'human-2', session='S-main')
        self.assertIn('キューの待ち: 3件', self.script('proposal.py', 'list').stdout)
        # Before the end main freezes: improve's proposals and its wait stop, people are still heard.
        self.script('proposal.py', 'freeze', '--note', 'final', session='S-main')
        self.assertIn(['main', 'freeze'], [line[:2] for line in self.progress()])
        self.assertEqual(self.script('proposal.py', 'wait', cwd=improve, session='S-improve', check=False).returncode, 4)
        self.git('switch', '-q', '-c', 'improve-b', 'main', cwd=improve)
        self.change(improve, 'b.txt', 'b\n', 'Improve b')
        self.assertEqual(self.submit_from(improve, 'S-improve').returncode, 4)
        # Only main runs the queue.
        for action in (['pending'], ['sync'], ['hold', 'human-3', '--until', 'x'], ['unhold', 'human-2'], ['requeue'],
                       ['decide', 'human-3', 'accepted'], ['freeze', '--off']):
            with self.subTest(action=action):
                refused = self.script('proposal.py', *action, cwd=improve, session='S-improve', check=False)
                self.assertEqual(refused.returncode, 1)
                self.assertIn('main', refused.stderr)
                self.assertIn('mainのセッション', self.guard('python3 scripts/proposal.py ' + ' '.join(action), 'S-improve'))
        self.assertIsNone(self.guard('python3 scripts/proposal.py wait', 'S-improve'))

    def test_merged_proposals_are_measuring_from_the_benchmark_and_dependents_fall_with_their_base(self):
        self.script('main-session.py', 'claim', session='S-main')
        self.commit_in_worktree()
        self.assertEqual(self.submit().returncode, 0)
        # A proposal built on idea/cache before main takes it stands or falls with it.
        stacked = Path(self.tmp.name) / 'repo-stacked'
        self.git('worktree', 'add', '-q', '-b', 'idea/stacked', str(stacked), 'idea/cache')
        self.branch_of[stacked] = 'idea/stacked'
        self.change(stacked, 'more.txt', 'more\n', 'Stack on cache')
        self.assertEqual(self.submit_from(stacked, 'S-worker').returncode, 0)
        stored = json.loads((self.repo / '.local/proposals/idea__stacked.json').read_text())
        self.assertEqual(stored['depends_on'], ['idea/cache'])
        self.git('merge', '-q', '--no-ff', '-m', 'Take idea/cache', 'idea/cache')
        self.assertIn('idea/cache', self.deliver('S-main'))
        self.assertEqual(self.status('idea/cache'), 'measuring')
        self.assertIn(['proposal', 'measuring'], [line[:2] for line in self.progress()])
        self.assertIn('キューの待ち: 1件', self.script('proposal.py', 'list').stdout)
        # The benchmark did not measure it (refused, aborted): main puts it back, still merged for the next one.
        self.assertIn('idea/cache', self.script('proposal.py', 'requeue', session='S-main').stdout)
        self.assertEqual(self.status('idea/cache'), 'merged')
        self.assertIn(['proposal', 'requeued'], [line[:2] for line in self.progress()])
        self.assertEqual(self.script('proposal.py', 'requeue', session='S-main', check=False).returncode, 1)
        self.deliver('S-main')
        self.assertEqual(self.status('idea/cache'), 'measuring')
        self.script('proposal.py', 'decide', 'idea/cache', 'rejected', '--run', 'ab12cd34', session='S-main')
        self.assertEqual(self.status('idea/stacked'), 'deferred')
        self.assertIn('idea/cache', self.script('proposal.py', 'show', 'idea/stacked').stdout)

    def test_main_brings_waiting_proposals_up_to_its_branch_and_resolves_a_conflict(self):
        self.script('main-session.py', 'claim', session='S-main')
        self.commit_in_worktree()  # changes line b
        self.assertEqual(self.submit().returncode, 0)
        other = self.add_worktree('other', 'idea/other')
        self.change(other, 'other.txt', 'o\n', 'Other file')
        self.assertEqual(self.submit_from(other, 'S-worker').returncode, 0)
        (self.repo / 'app.txt').write_text('a\nbee\nc\n')
        self.git('commit', '-qam', 'Main changes b too')
        self.deliver('S-main')
        self.assertEqual(self.status('idea/cache'), 'needs-sync')
        busy = self.script('proposal.py', 'sync', session='S-main').stdout
        self.assertIn('idea/other: main', busy)  # merged main into idea/other
        self.assertIn('衝突', busy)
        work = self.repo / '.local/proposal-sync'
        self.assertIn('<<<<<<<', (work / 'app.txt').read_text())
        refused = self.script('proposal.py', 'sync', session='S-main', check=False)
        self.assertEqual(refused.returncode, 1)
        self.assertIn('解消中', refused.stderr)
        (work / 'app.txt').write_text('a\nBEE\nc\n')
        self.git('add', 'app.txt', cwd=work)
        done = self.script('proposal.py', 'sync', session='S-main').stdout
        self.assertIn('idea/cache: mainを取り込んだ', done)
        # A conflicting proposal was not handed over before; now it merges cleanly, main reads it once.
        self.assertEqual(self.status('idea/cache'), 'submitted')
        stored = json.loads((self.repo / '.local/proposals/idea__cache.json').read_text())
        self.assertEqual(stored['commit'], self.git('rev-parse', 'idea/cache').strip())
        self.assertIn('### idea/cache', self.deliver('S-main'))
        # One main had read keeps that mark across the sync: main made the merge, it is not news.
        stored = json.loads((self.repo / '.local/proposals/idea__other.json').read_text())
        self.assertEqual(stored['delivered']['S-main'], stored['commit'])
        self.assertEqual(stored['commit'], self.git('rev-parse', 'idea/other').strip())
        self.assertEqual(self.deliver('S-main'), '')
        self.assertIn('最新', self.script('proposal.py', 'sync', session='S-main').stdout)
        self.git('merge', '-q', '--no-ff', '-m', 'Take idea/cache', 'idea/cache')
        self.assertEqual(self.git('show', 'main:app.txt'), 'a\nBEE\nc\n')
        self.assertIn(['proposal', 'synced'], [line[:2] for line in self.progress()])

    def test_a_branch_checked_out_by_its_author_is_left_alone(self):
        self.script('main-session.py', 'claim', session='S-main')
        self.commit_in_worktree('own.txt', 'own\n', 'Own file')
        self.assertEqual(self.submit().returncode, 0)
        # submit left the branch; an author who checks it out again keeps main from moving it.
        self.assertEqual(self.git('rev-parse', '--abbrev-ref', 'HEAD', cwd=self.worktree).strip(), 'HEAD')
        self.git('switch', '-q', 'idea/cache', cwd=self.worktree)
        (self.repo / 'main.txt').write_text('m\n')
        self.git('add', 'main.txt')
        self.git('commit', '-qm', 'Main moves on')
        self.assertIn('checkoutされている', self.script('proposal.py', 'sync', session='S-main').stdout)


if __name__ == '__main__':
    unittest.main()
