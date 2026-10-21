from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import unittest

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

    def test_follow_prints_only_what_is_appended_for_the_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'progress.log'
            log.write_text('2026-10-21T10:00:00+0900\tmain\tanalyzed\told\n')
            env = dict(os.environ, PROGRESS_LOG=str(log))
            follower = subprocess.Popen(['python3', str(ROOT / 'scripts/progress.py'), 'follow', 'improve'], env=env,
                                        stdout=subprocess.PIPE, text=True)
            try:
                time.sleep(1)
                with log.open('a') as out:
                    out.write('2026-10-21T10:01:00+0900\tproposal\tsynced\tskip\n')
                    out.write('2026-10-21T10:02:00+0900\tmain\tanalyzed\tnew 次の最大の制約: DB\n')
                time.sleep(1.5)
            finally:
                follower.terminate()
            out = follower.communicate(timeout=5)[0]
        self.assertNotIn('old', out)
        self.assertNotIn('skip', out)
        self.assertIn('10:02 main analyzed new 次の最大の制約: DB → ', out)

    def test_a_log_that_appears_after_following_started_is_read_from_its_first_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'progress.log'
            env = dict(os.environ, PROGRESS_LOG=str(log))
            follower = subprocess.Popen(['python3', str(ROOT / 'scripts/progress.py'), 'follow', 'improve'], env=env,
                                        stdout=subprocess.PIPE, text=True)
            try:
                time.sleep(1)
                log.write_text('2026-10-21T10:00:00+0900\tkickoff\tcode-ready\twebapp/rust\n')
                time.sleep(1.5)
            finally:
                follower.terminate()
            out = follower.communicate(timeout=5)[0]
        self.assertIn('10:00 kickoff code-ready webapp/rust', out)

    def test_lanes_that_stop_short_and_unfreezing_reach_someone(self):
        self.assertIn('止まった', self.shown('main', 'lane:full-memory', 'timeout'))
        self.assertIn('止まった', self.shown('main', 'lane:full-memory', 'stopped'))
        self.assertIn('再開', self.shown('improve', 'proposal', 'unfrozen'))


if __name__ == '__main__':
    unittest.main()
