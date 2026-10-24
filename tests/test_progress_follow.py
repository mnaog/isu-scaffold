import contextlib
import io
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import progress  # noqa: E402


class FollowTests(unittest.TestCase):
    def setUp(self):
        self.rules = progress.load_rules(ROOT)

    def shown(self, role, source, state, message='x'):
        return progress.for_role(self.rules, role, f'2026-10-21T10:42:00+0900\t{source}\t{state}\t{message}')

    def test_each_role_gets_its_own_lines_with_what_to_do(self):
        self.assertIn('→ mainの分析', self.shown('improve', 'main', 'analyzed', 'aaaa score 1'))
        self.assertIsNone(self.shown('main', 'main', 'analyzed'))  # its own line
        self.assertIn('新しい投稿', self.shown('main', 'proposal', 'submitted'))
        self.assertIsNone(self.shown('improve', 'proposal', 'submitted'))
        self.assertIsNone(self.shown('main', 'proposal', 'synced'))  # the record only
        self.assertIsNone(self.shown('improve', 'proposal', 'synced'))
        self.assertIsNotNone(self.shown('improve', 'lane:indexes', 'commit'))
        self.assertIsNone(self.shown('improve', 'lane:indexes', 'running'))
        self.assertIn('失敗', self.shown('main', 'lane:full-memory', 'failed'))
        self.assertIn('止める', self.shown('main', 'benchmark', 'slow'))
        self.assertIn('10:42 kickoff code-ready', self.shown('improve', 'kickoff', 'code-ready', ''))
        self.assertIn('宛先の表', self.shown('main', 'something', 'new'))  # unknown lines reach main
        self.assertIsNone(self.shown('improve', 'something', 'new'))

    def test_the_end_of_a_benchmark_brings_its_facts_and_brief_to_main(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '.local/bench-results').mkdir(parents=True)
            (Path(tmp) / '.local/bench-results/abcd1234.txt').write_text('# run abcd1234 PASS 123456\n## brief\nx\n')
            line = '2026-10-21T10:42:00+0900\tbenchmark\tfinished\tabcd1234 PASS score 123456'
            shown = progress.for_role(self.rules, 'main', line, tmp)
            self.assertTrue(shown.startswith('10:42 benchmark finished abcd1234 PASS score 123456 → '), shown)
            self.assertTrue(shown.endswith('# run abcd1234 PASS 123456\n## brief\nx'), shown)
            self.assertIsNone(progress.for_role(self.rules, 'improve', line, tmp))
            missing = progress.for_role(self.rules, 'main', line.replace('abcd1234', 'ffff0000'), tmp)
            self.assertNotIn('\n', missing)  # the line alone when the file is gone
            # A first word that is not a plain file name attaches nothing.
            self.assertNotIn('\n', progress.for_role(self.rules, 'main', line.replace('abcd1234', '../x'), tmp))

    def follow_improve(self, log, *steps):
        """Follow `log` for improve. Each step runs just before one of the follower's polls, so the follower
        has polled once between two steps however slow the machine is; it stops after the last step's poll."""
        steps = list(steps)

        def stop():
            if not steps:
                return True
            steps.pop(0)()
            return False

        with mock.patch.dict(os.environ, PROGRESS_LOG=str(log)), contextlib.redirect_stdout(io.StringIO()) as out:
            progress.follow('improve', root=ROOT, interval=0.01, stop=stop)
        return out.getvalue()

    def test_follow_prints_only_what_is_appended_for_the_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'progress.log'
            log.write_text('2026-10-21T10:00:00+0900\tmain\tanalyzed\told\n')

            def append():
                with log.open('a') as out:
                    out.write('2026-10-21T10:01:00+0900\tproposal\tsynced\tskip\n')
                    out.write('2026-10-21T10:02:00+0900\tmain\tanalyzed\tnew 次の最大の制約: DB\n')

            # The first poll takes the end of the file; the lines appended after it are the ones shown.
            out = self.follow_improve(log, lambda: None, append)
        self.assertNotIn('old', out)
        self.assertNotIn('skip', out)
        self.assertIn('10:02 main analyzed new 次の最大の制約: DB → ', out)

    def test_a_log_that_appears_after_following_started_is_read_from_its_first_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'progress.log'
            # The first poll finds no file; the file that appears before the next is read from its first line.
            out = self.follow_improve(log, lambda: None, lambda: log.write_text(
                '2026-10-21T10:00:00+0900\tkickoff\tcode-ready\twebapp/rust\n'))
        self.assertIn('10:00 kickoff code-ready webapp/rust', out)

    def test_lanes_that_stop_short_and_unfreezing_reach_someone(self):
        self.assertIn('止まった', self.shown('main', 'lane:full-memory', 'timeout'))
        self.assertIn('止まった', self.shown('main', 'lane:full-memory', 'stopped'))
        self.assertIn('再開', self.shown('improve', 'proposal', 'unfrozen'))


if __name__ == '__main__':
    unittest.main()
