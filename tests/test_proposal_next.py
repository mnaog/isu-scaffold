"""proposal.py next: from a finished run to the next benchmark, in an order that keeps the code and the
proposals in step, and only the detailed analysis told to improve."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tests'))
import isuscope_stub  # noqa: E402
GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'}
BASE = '01a15800-0000-7000-8000-00000000bbbb'
RUN = '01a15900-0000-7000-8000-00000000aaaa'


class ProposalNextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        (self.repo / 'scripts').mkdir(parents=True)
        for name in ('main-session.py', 'proposal.py', 'isuscope_sql.py', 'progress.py'):
            shutil.copy(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        (self.repo / 'config').mkdir()
        shutil.copy(ROOT / 'config/progress.json', self.repo / 'config/progress.json')
        (self.repo / 'app.txt').write_text('a\n')
        (self.repo / '.gitignore').write_text('.local/\n__pycache__/\n')
        self.db, bin_dir = isuscope_stub.install(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ('CLAUDE_CODE_SESSION_ID', 'CODEX_SESSION_ID', 'CODEX_THREAD_ID', 'ISUCON_LANE')}
        self.env.update(GIT_ENV, PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}', PROPOSAL_AUTO_PREBUILD='0')
        self.git('init', '-q', '-b', 'main')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.script('main-session.py', 'claim', session='S-main')
        spec = importlib.util.spec_from_file_location('proposal', self.repo / 'scripts/proposal.py')
        self.proposal = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.proposal)

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.repo, check=True, capture_output=True,
                              text=True, env=self.env).stdout.strip()

    def script(self, name, *args, cwd=None, session=None, check=True):
        env = dict(self.env, CLAUDE_CODE_SESSION_ID=session) if session else self.env
        result = subprocess.run([sys.executable, str(self.repo / 'scripts' / name), *args], cwd=cwd or self.repo,
                                capture_output=True, text=True, env=env)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def submit(self, branch, path, on=None):
        """A person's proposal from its own worktree (on another proposal, `on`)."""
        worktree = Path(self.tmp.name) / f'{branch}-repo'
        self.git('worktree', 'add', '-q', '-b', branch, str(worktree), on or 'main')
        (worktree / path).write_text(branch + '\n')
        self.git('add', path, cwd=worktree)
        self.git('commit', '-qm', f'Change {path}', cwd=worktree)
        self.script('proposal.py', 'submit', '--hypothesis', f'{branch}で速くなる', '--expect', f'{path}が効く',
                    cwd=worktree, session=f'S-{branch}')

    def measure(self, *branches, passed=1):
        """main merges the branches, the benchmark starts (the hook marks them measuring), and run RUN ends."""
        for branch in branches:
            self.git('merge', '-q', '--no-ff', '--no-edit', branch)
        with self.env_for_module():
            self.proposal.start_measuring(self.repo)
        head = self.git('rev-parse', 'HEAD')
        self.db.execute("INSERT INTO runs (id, started_at, finished_at, score, passed, commit_hash) "
                        "VALUES (?, '2026-10-24T01:00:00+00:00', '2026-10-24T01:01:00+00:00', 1000, 1, ?)", (BASE, head))
        self.db.execute("INSERT INTO runs (id, started_at, finished_at, score, passed, commit_hash, analysis_status) "
                        "VALUES (?, '2026-10-24T02:00:00+00:00', '2026-10-24T02:01:30+00:00', 1100, ?, ?, ?)",
                        (RUN, passed, head, 'pending' if passed else 'not_required'))
        self.db.execute("INSERT INTO run_bases VALUES (?, ?, '')", (RUN, BASE))
        self.db.commit()

    def env_for_module(self):
        """The module calls git and isuscope in-process: give it this test's PATH and identity."""
        import contextlib
        from unittest import mock

        @contextlib.contextmanager
        def patched():
            with mock.patch.dict(os.environ, self.env, clear=True):
                yield
        return patched()

    def next(self, *args, check=True):
        return self.script('proposal.py', 'next', RUN[-8:], *args, session='S-main', check=check)

    def status(self, branch):
        return json.loads((self.repo / f'.local/proposals/{branch}.json').read_text())

    def analyses(self):
        return self.db.execute('SELECT verdict, body FROM run_analyses WHERE run_id = ? ORDER BY created_at',
                               (RUN,)).fetchall()

    def progress(self):
        path = self.repo / '.local/progress.log'
        return [line.split('\t')[1:] for line in path.read_text().splitlines()] if path.exists() else []

    def test_records_the_decision_then_merges_the_next_one(self):
        self.submit('human1-a', 'a.txt')
        self.submit('human2-b', 'b.txt')
        self.measure('human1-a')
        done = self.next('--verdict', 'supported', '--note', '+10%、振れ幅±3%を超える', '--decide', 'human1-a=accepted',
                         '--merge', 'human2-b')
        decision = self.status('human1-a')['decision']
        self.assertEqual((decision['decision'], decision['run']), ('accepted', RUN[-8:]))
        self.assertEqual(self.status('human2-b')['status'], 'merged')
        self.assertTrue((self.repo / 'b.txt').exists())
        self.assertEqual(self.analyses(), [('supported', '+10%、振れ幅±3%を超える')])
        cited = self.db.execute("SELECT c.run_id FROM change_decision_runs c JOIN change_decisions d ON d.id = c.decision_id "
                                "WHERE d.change_id = 'human1-a'").fetchall()
        self.assertEqual(sorted(row[0] for row in cited), sorted([RUN, BASE]))
        self.assertIn(f'make bench BASE={RUN[-8:]}', done.stdout)
        self.assertIn(['proposal', 'accepted'], [line[:2] for line in self.progress()])
        # The short analysis is not improve's material; the detailed one, appended during the next benchmark, is.
        self.assertNotIn(['main', 'analyzed'], [line[:2] for line in self.progress()])
        self.script('proposal.py', 'adopt', session='S-main')
        self.assertNotIn(['main', 'analyzed'], [line[:2] for line in self.progress()])
        subprocess.run(['isuscope', 'analyze', RUN, 'supported', '--analysis', '詳しく: INSERTが1/5'], cwd=self.repo,
                       env=self.env, check=True, capture_output=True)
        self.script('proposal.py', 'adopt', session='S-main')
        told = [line for line in self.progress() if line[:2] == ['main', 'analyzed']]
        self.assertEqual(len(told), 1)
        self.assertIn('詳しく: INSERTが1/5', told[0][2])

    def test_a_rejected_one_is_reverted_and_takes_the_ones_built_on_it(self):
        self.submit('human1-a', 'a.txt')
        self.submit('human1-b', 'b.txt', on='human1-a')  # built on a before main took it
        self.submit('human2-c', 'c.txt')
        self.assertEqual(self.status('human1-b')['depends_on'], ['human1-a'])
        self.measure('human1-a')
        self.next('--verdict', 'rejected', '--note', '-2%、振れ幅の中', '--decide', 'human1-a=rejected', '--merge', 'human2-c')
        self.assertEqual(self.status('human1-a')['status'], 'rejected')
        self.assertEqual(self.status('human1-b')['status'], 'deferred')
        self.assertFalse((self.repo / 'a.txt').exists())  # reverted
        self.assertTrue((self.repo / 'c.txt').exists())

    def test_one_built_on_a_rejected_one_and_merged_already_goes_with_it(self):
        # main merged b (built on a) before deciding a, as it used to while the benchmark ran.
        self.submit('human1-a', 'a.txt')
        self.submit('human1-b', 'b.txt', on='human1-a')
        self.measure('human1-a')
        self.git('merge', '-q', '--no-ff', '--no-edit', 'human1-b')
        self.next('--verdict', 'rejected', '--note', '効かない', '--decide', 'human1-a=rejected')
        self.assertEqual(self.status('human1-b')['status'], 'deferred')
        self.assertFalse((self.repo / 'a.txt').exists())
        self.assertFalse((self.repo / 'b.txt').exists())

    def test_a_dependent_cannot_be_merged_after_its_base_is_rejected(self):
        self.submit('human1-a', 'a.txt')
        self.submit('human1-b', 'b.txt', on='human1-a')
        self.measure('human1-a')
        refused = self.next('--verdict', 'rejected', '--note', '効かない', '--decide', 'human1-a=rejected',
                            '--merge', 'human1-b', check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('human1-bは取り込めない: deferred', refused.stderr)
        self.assertFalse((self.repo / 'b.txt').exists())

    def test_every_measured_proposal_needs_a_decision_and_nothing_is_recorded_without(self):
        self.submit('human1-a', 'a.txt')
        self.submit('human2-b', 'b.txt')
        self.measure('human1-a', 'human2-b')
        refused = self.next('--verdict', 'supported', '--note', 'x', '--decide', 'human1-a=accepted', check=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('採否が無い: human2-b', refused.stderr)
        self.assertEqual(self.analyses(), [])
        self.assertEqual(self.status('human1-a')['status'], 'measuring')
        self.assertNotEqual(self.next('--note', 'x', '--decide', 'human1-a=accepted', '--decide', 'human2-b=rejected',
                                      check=False).returncode, 0)  # a PASS run needs its verdict

    def test_the_same_command_again_finds_everything_done(self):
        self.submit('human1-a', 'a.txt')
        self.submit('human2-b', 'b.txt')
        self.measure('human1-a')
        args = ('--verdict', 'supported', '--note', '+10%', '--decide', 'human1-a=accepted', '--merge', 'human2-b')
        self.next(*args)
        again = self.next(*args)
        self.assertIn('human2-bはもう取り込んである', again.stdout)
        self.assertEqual(len(self.analyses()), 1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM change_decisions').fetchone()[0], 1)

    def test_a_run_that_did_not_pass_puts_its_proposals_back_or_rejects_them(self):
        self.submit('human1-a', 'a.txt')
        self.measure('human1-a', passed=0)
        self.assertIn('判定を書けない', self.next('--verdict', 'rejected', '--note', 'x', '--requeue', check=False).stderr)
        self.next('--requeue')
        self.assertEqual(self.status('human1-a')['status'], 'merged')  # measured again on the next run
        self.assertTrue((self.repo / 'a.txt').exists())

    def test_a_proposal_that_broke_the_run_is_rejected_with_its_own_reason(self):
        self.submit('human1-a', 'a.txt')
        self.measure('human1-a', passed=0)
        self.next('--decide', 'human1-a=rejected:500を返した')
        self.assertEqual(self.status('human1-a')['status'], 'rejected')
        self.assertEqual(self.status('human1-a')['decision']['note'], '500を返した')
        self.assertFalse((self.repo / 'a.txt').exists())  # reverted
        self.assertEqual(self.analyses(), [])  # a FAIL run gets no analysis

    def test_a_note_that_starts_with_a_dash_stays_the_note(self):
        # argparse before Python 3.14 reads such a value as the next option; the arguments are joined first.
        self.assertEqual(self.proposal.with_text_values(['next', 'x', '--note', '-2%', '--decide', 'a=rejected:-1%']),
                         ['next', 'x', '--note=-2%', '--decide', 'a=rejected:-1%'])

    def test_the_decision_points_to_the_run_measured_not_its_base(self):
        # `isuscope analyze --change` cites the run and its older base; the proposal gets the run.
        self.submit('human1-a', 'a.txt')
        self.measure('human1-a')
        subprocess.run(['isuscope', 'analyze', RUN, 'supported', '--analysis', '効いた', '--change', 'human1-a',
                        '--decision', 'accepted'], cwd=self.repo, env=self.env, check=True, capture_output=True)
        self.script('proposal.py', 'adopt', session='S-main')
        self.assertEqual(self.status('human1-a')['decision']['run'], RUN[-8:])

    def test_runs_left_with_only_the_short_analysis_are_listed(self):
        self.measure()
        self.next('--verdict', 'supported', '--note', '同じ構成')
        with self.env_for_module():
            self.assertEqual(self.proposal.analyses_owed(self.repo), [RUN])
        self.assertIn(f'詳しい分析がまだのrun: {RUN[-8:]}', self.script('proposal.py', 'freeze', session='S-main').stdout)
        subprocess.run(['isuscope', 'analyze', RUN, 'supported', '--analysis', '詳しく'], cwd=self.repo, env=self.env,
                       check=True, capture_output=True)
        with self.env_for_module():
            self.assertEqual(self.proposal.analyses_owed(self.repo), [])

    def test_only_main_moves_on(self):
        self.measure()
        refused = self.script('proposal.py', 'next', RUN[-8:], '--verdict', 'skipped', '--note', 'x',
                              session='S-other', check=False)
        self.assertIn('/mainで宣言したセッション）の操作', refused.stderr)


if __name__ == '__main__':
    unittest.main()
