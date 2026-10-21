import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / 'scripts/bench-hook.py'
GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@example.com',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@example.com'}


class ProposalDecisionTest(unittest.TestCase):
    """main's decision belongs to the commit it merged and measured, and a merged proposal waits for one."""

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
        self.worktree = Path(self.tmp.name) / 'repo-human'
        self.git('worktree', 'add', '-q', '-b', 'human', str(self.worktree))
        self.script('main-session.py', 'claim', session='S-main')

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, cwd=None):
        return subprocess.run(['git', *args], cwd=cwd or self.repo, check=True, capture_output=True,
                              text=True, env=self.env).stdout.strip()

    def script(self, name, *args, cwd=None, session=None, check=True):
        env = dict(self.env, CLAUDE_CODE_SESSION_ID=session) if session else self.env
        result = subprocess.run(['python3', str(self.repo / 'scripts' / name), *args], cwd=cwd or self.repo,
                                capture_output=True, text=True, env=env)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def commit_and_submit(self, path, hypothesis):
        self.git('switch', '-q', 'human', cwd=self.worktree)  # submit left the branch; the author comes back to it
        (self.worktree / path).write_text(hypothesis + '\n')
        self.git('add', path, cwd=self.worktree)
        self.git('commit', '-qm', hypothesis, cwd=self.worktree)
        self.script('proposal.py', 'submit', '--hypothesis', hypothesis, '--expect', 'x', '--verified', 'y',
                    cwd=self.worktree, session='S-human')
        return self.git('rev-parse', 'HEAD', cwd=self.worktree)

    def decide(self, *args, check=True):
        return self.script('proposal.py', 'decide', 'human', *args, session='S-main', check=check)

    def deliver(self):
        payload = {'tool_name': 'Bash', 'session_id': 'S-main', 'cwd': str(self.repo),
                   'tool_input': {'command': 'isuscope run --hypothesis x', 'run_in_background': True}}
        result = subprocess.run(['python3', str(HOOK), '--post'], input=json.dumps(payload), text=True,
                                capture_output=True, env=dict(self.env, TOOL_GUARD_ROOT=str(self.repo)))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['hookSpecificOutput']['additionalContext'] if result.stdout.strip() else ''

    def test_a_decision_goes_to_the_merged_commit_when_the_author_submitted_again(self):
        # seventh: main measured the batched INSERT while the person submitted a MySQL change on the
        # same branch, and `accepted` landed on the MySQL change nobody had merged or measured.
        batched = self.commit_and_submit('insert.txt', '座標をまとめてINSERTする')
        self.assertIn('### human', self.deliver())
        self.git('merge', '-q', '--no-ff', '-m', 'Take human', 'human')
        self.assertIn('human', self.script('proposal.py', 'list').stdout)  # merged, not decided: still listed
        mysql = self.commit_and_submit('mysql.txt', 'redoを2GiBにする')

        context = self.deliver()
        self.assertIn('### human', context)  # the new commit
        self.assertIn(f'取り込んだが採否を記録していない投稿: human（{batched[:8]}）', context)
        self.assertIn('--run', self.decide('accepted', check=False).stderr)
        told = self.decide('accepted', '--run', 'aaaa1111', '--note', '+20%').stdout
        self.assertIn(batched[:8], told)
        self.assertIn(f'今の投稿{mysql[:8]}', told)

        data = json.loads((self.repo / '.local/proposals/human.json').read_text())
        self.assertEqual((data['commit'], data['status'], data.get('decision')), (mysql, 'delivered', None))
        self.assertEqual([(e['commit'], e['status'], e['decision']['run']) for e in data['history']],
                         [(batched, 'accepted', 'aaaa1111')])
        shown = self.script('proposal.py', 'show', 'human').stdout
        self.assertIn(f'前の投稿 {batched[:8]}: accepted  run aaaa1111', shown)
        self.assertNotIn('採否を記録していない', self.deliver())

        # The MySQL change was never merged: it cannot be accepted, and a rejection is its own.
        self.assertIn('入っていない', self.decide('accepted', '--run', 'bbbb2222', check=False).stderr)
        self.assertIn(mysql[:8], self.decide('rejected', '--note', '測る前に見送り').stdout)
        data = json.loads((self.repo / '.local/proposals/human.json').read_text())
        self.assertEqual((data['status'], data['decision']['commit']), ('rejected', mysql))
        self.assertEqual(data['history'][0]['decision']['decision'], 'accepted')

    def test_a_merged_proposal_is_owed_a_decision_at_every_benchmark(self):
        commit = self.commit_and_submit('insert.txt', '座標をまとめてINSERTする')
        self.deliver()
        self.git('merge', '-q', '--no-ff', '-m', 'Take human', 'human')
        for _ in range(2):
            self.assertIn(f'human（{commit[:8]}）', self.deliver())
        self.assertIn('採否はまだ出ていない', self.script('proposal.py', 'show', 'human').stdout)
        self.decide('accepted', '--run', 'aaaa1111')
        self.assertEqual(self.deliver(), '')
        self.assertNotIn('human', self.script('proposal.py', 'list').stdout)
        explicit = self.decide('deferred', '--commit', commit[:8]).stdout
        self.assertIn('deferred', explicit)

    def test_a_decision_on_a_later_commit_covers_the_earlier_ones_it_holds(self):
        self.commit_and_submit('insert.txt', '座標をまとめてINSERTする')
        self.deliver()
        self.git('merge', '-q', '--no-ff', '-m', 'Take human', 'human')
        later = self.commit_and_submit('more.txt', 'まとめる件数を増やす')  # on top of the merged one
        self.git('merge', '-q', '--no-ff', '-m', 'Take human again', 'human')
        self.assertIn(later[:8], self.decide('accepted', '--run', 'cccc3333').stdout)
        self.assertNotIn('採否を記録していない', self.deliver())

    def analyze(self):
        payload = {'tool_name': 'Bash', 'session_id': 'S-main', 'cwd': str(self.repo),
                   'tool_input': {'command': 'isuscope analyze 0000aaaa supported --analysis x --change human --decision accepted'}}
        result = subprocess.run(['python3', str(HOOK), '--post'], input=json.dumps(payload), text=True,
                                capture_output=True, env=dict(self.env, TOOL_GUARD_ROOT=str(self.repo)))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)['hookSpecificOutput']['additionalContext'] if result.stdout.strip() else ''

    def test_the_decision_main_records_in_isuscope_reaches_the_proposal_and_improve_hears_once(self):
        self.commit_and_submit('insert.txt', '座標をまとめてINSERTする')
        self.deliver()
        self.git('merge', '-q', '--no-ff', '-m', 'Take human', 'human')
        self.deliver()  # the benchmark starts: human is measured
        self.assertEqual(json.loads((self.repo / '.local/proposals/human.json').read_text())['status'], 'measuring')
        run_id = '01a15900-0000-7000-8000-00000000aaaa'
        run = self.repo / 'isuscope-data/runs' / run_id
        run.mkdir(parents=True)
        (run / 'run.json').write_text(json.dumps({'id': run_id, 'benchmark': {'score': 720000}, 'analyses': [
            {'id': 'an-1', 'created_at': '2999-01-01T00:00:00.123456789Z', 'verdict': 'supported',
             'body': '次の最大の制約: POST /coordinate のDB書き込み。\nINSERTが1/5に減った。'}]}))
        decisions = self.repo / 'isuscope-data/changes/human/decisions'
        decisions.mkdir(parents=True)
        (decisions / 'd.json').write_text(json.dumps({
            'id': 'd1', 'change_id': 'human', 'created_at': '2999-01-01T00:00:00.123456789Z', 'status': 'accepted',
            'reason': 'INSERTが1/5に減り、score +12%', 'evidence': [{'run_id': run_id}]}))

        told = self.analyze()
        self.assertIn('human accepted', told)
        self.assertIn('main analyzed', told)
        data = json.loads((self.repo / '.local/proposals/human.json').read_text())
        self.assertEqual((data['status'], data['decision']['run']), ('accepted', '0000aaaa'))
        self.assertIn('score +12%', data['decision']['note'])
        lines = (self.repo / '.local/progress.log').read_text().splitlines()
        analyzed = [line for line in lines if '\tmain\tanalyzed\t' in line]
        self.assertEqual(len(analyzed), 1)
        self.assertIn('0000aaaa score 720000: 次の最大の制約: POST /coordinate', analyzed[0])
        self.assertEqual(self.analyze(), '')  # the same analysis again: nothing new to copy or tell


if __name__ == '__main__':
    unittest.main()
