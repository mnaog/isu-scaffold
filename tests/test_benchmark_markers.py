import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = """\
10:49:01.100 webappの初期化を開始します
10:49:01.200 [ADMIN] 初期化 internal detail
10:49:09.800 初期化が完了しました
10:49:09.900 負荷走行を開始します
10:50:22.022 score: 27845 (= 27845 - 0) : passed
"""


class BenchmarkMarkersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'config').mkdir()
        shutil.copy(ROOT / 'scripts/benchmark-markers.py', self.root / 'scripts/benchmark-markers.py')
        (self.root / 'config/isuscope-policy.json').write_text(json.dumps(
            {'history_dir': 'docs/agent-history', 'operator_line_pattern': '\\[ADMIN\\]'}))
        (self.root / 'config/benchmark-sample.log').write_text(SAMPLE)

    def tearDown(self):
        self.tmp.cleanup()

    def markers(self, *args):
        return subprocess.run(['python3', 'scripts/benchmark-markers.py', *args], cwd=self.root,
                              capture_output=True, text=True, env={**os.environ, 'REPO_DIR': str(self.root)})

    def test_suggest_offers_participant_lines_without_timestamps(self):
        output = self.markers('suggest').stdout
        self.assertIn('webappの初期化を開始します', output)
        self.assertIn('負荷走行を開始します', output)
        self.assertNotIn('ADMIN', output)
        self.assertNotIn('10:49', output)

    def test_set_checks_order_and_writes_the_policy(self):
        self.assertNotEqual(self.markers('check').returncode, 0)
        self.assertNotEqual(self.markers('set', '負荷走行を開始します', '初期化を開始').returncode, 0)
        self.assertNotEqual(self.markers('set', 'internal detail', '負荷走行').returncode, 0)  # operator line
        result = self.markers('set', 'webappの初期化を開始します', '負荷走行を開始します')
        self.assertEqual(result.returncode, 0, result.stderr)
        policy = json.loads((self.root / 'config/isuscope-policy.json').read_text())
        self.assertEqual(policy['initialize_start_marker'], 'webappの初期化を開始します')
        self.assertEqual(policy['operator_line_pattern'], '\\[ADMIN\\]')
        self.assertEqual(self.markers('check').returncode, 0)


if __name__ == '__main__':
    unittest.main()
