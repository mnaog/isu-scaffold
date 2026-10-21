import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts/board'))
import variance  # noqa: E402


class SpreadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.minute = 0

    def tearDown(self):
        self.tmp.cleanup()

    def run_record(self, short, score, files, passed=True, repeat=False, tags=()):
        self.minute += 1
        run_id = f'01a15900-0000-7000-8000-0000{short}'
        directory = self.repo / 'isuscope-data/runs' / run_id
        directory.mkdir(parents=True)
        (directory / 'run.json').write_text(json.dumps({
            'id': run_id, 'state': 'complete', 'started_at': f'2026-10-20T12:{self.minute:02d}:00Z',
            'repeat': repeat, 'tags': list(tags), 'benchmark': {'score': score, 'passed': passed},
            'identity': {'values': {'deployed_files': files}}}))
        return run_id

    def test_each_run_carries_the_swing_known_when_it_started(self):
        first = self.run_record('aaaa0001', 700000, 'cfg-a')
        self.run_record('aaaa0002', 720000, 'cfg-a', repeat=True)
        later = self.run_record('bbbb0001', 800000, 'cfg-b', tags=['wait', 'wait:GET /a'])
        failed = self.run_record('bbbb0002', 100, 'cfg-b', passed=False)  # a FAIL counts for nothing
        known = variance.spread(self.repo)
        self.assertIsNone(known[first]['spread'])  # nothing measured twice yet
        swing = known[later]['spread']
        # 710000 ± 10000: each ±1/71, pooled over one degree of freedom
        self.assertAlmostEqual(swing['sigma'], (2 * (10000 / 710000) ** 2) ** 0.5, places=9)
        self.assertEqual((swing['groups'], swing['runs']), (1, 2))
        self.assertTrue(known['01a15900-0000-7000-8000-0000aaaa0002']['repeat'])
        self.assertTrue(known[later]['wait'])
        self.assertEqual(known[failed]['spread'], swing)


if __name__ == '__main__':
    unittest.main()
