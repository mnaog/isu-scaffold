import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class SpreadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / 'scripts').mkdir()
        shutil.copy(ROOT / 'scripts/spread.py', self.repo / 'scripts/spread.py')
        self.minute = 0

    def tearDown(self):
        self.tmp.cleanup()

    def run_record(self, short, score, files, passed=True, state='complete'):
        """A run of the configuration whose deployed files hash to `files`."""
        self.minute += 1
        run_id = f'01a15900-0000-7000-8000-0000{short}'
        directory = self.repo / 'isuscope-data/runs' / run_id
        directory.mkdir(parents=True)
        (directory / 'run.json').write_text(json.dumps({
            'id': run_id, 'mode': 'run', 'state': state, 'started_at': f'2026-10-20T12:{self.minute:02d}:00Z',
            'benchmark': {'score': score, 'passed': passed},
            'identity': {'values': {'build': 'deployed', 'deployed_files': files, 'measurement': 'm1'}}}))

    def spread(self, *args):
        result = subprocess.run(['python3', 'scripts/spread.py', *args], cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_the_base_configuration_s_own_runs(self):
        for short, score in (('aaaa0001', 700000), ('aaaa0002', 720000), ('aaaa0003', 680000)):
            self.run_record(short, score, 'cfg-a')
        self.run_record('bbbb0001', 650000, 'cfg-b', passed=False)  # a FAIL counts for nothing
        out = self.spread('--base', 'aaaa0002')
        self.assertIn('run aaaa0002の構成', out)
        self.assertIn('3回: 700000（aaaa0001） 720000（aaaa0002） 680000（aaaa0003）', out)
        self.assertIn('平均 700000、平均からのずれ -2.9%〜+2.9%', out)
        self.assertNotIn('直近に2回以上測った構成', out)
        data = json.loads(self.spread('--base', 'aaaa0001', '--json'))
        self.assertEqual(data['base']['runs'], 3)
        self.assertIsNone(data['other'])

    def test_a_configuration_measured_once_shows_the_latest_measured_again(self):
        for short, score in (('aaaa0001', 700000), ('aaaa0002', 770000), ('aaaa0003', 630000)):
            self.run_record(short, score, 'cfg-a')
        self.run_record('cccc0001', 800000, 'cfg-c')
        out = self.spread('--base', 'cccc0001')
        self.assertIn('1回: 800000（cccc0001）', out)
        self.assertIn('直近に2回以上測った構成（最後のrun aaaa0003', out)
        self.assertIn('平均からのずれ -10.0%〜+10.0%', out)
        self.assertEqual(json.loads(self.spread('--base', 'cccc0001', '--json'))['other']['last_run'], 'aaaa0003')
        # Without --base, and without an identity script to ask, the last run's configuration is the base.
        self.assertIn('最後のrun cccc0001の構成', self.spread())

    def test_two_runs_of_the_base_are_enough_to_list_alone(self):
        self.run_record('aaaa0001', 700000, 'cfg-a')
        self.run_record('aaaa0002', 720000, 'cfg-a')
        self.run_record('cccc0001', 800000, 'cfg-c')
        self.run_record('cccc0002', 800000, 'cfg-c')
        out = self.spread('--base', 'cccc0001')
        self.assertIn('平均からのずれ +0.0%〜+0.0%', out)  # no swing at all is an answer too
        self.assertNotIn('aaaa0001', out)

    def test_nothing_measured_twice_yet_says_so(self):
        self.run_record('aaaa0001', 700000, 'cfg-a')
        out = self.spread('--base', 'aaaa0001')
        self.assertIn('同じ構成を2回以上測ったrunはまだ無い', out)


if __name__ == '__main__':
    unittest.main()
