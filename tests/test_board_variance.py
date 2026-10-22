import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts/board'))
import variance  # noqa: E402


class SpreadTests(unittest.TestCase):
    """The board takes the swing from `isuscope variance`, which owns the arithmetic."""

    def test_each_run_carries_the_swing_isuscope_knew_when_it_started(self):
        rows = [
            {'run': 'aaaa0001', 'config_key': 'a', 'score': 700000, 'passed': True, 'repeat': False,
             'spread': None, 'spread_configs': 0, 'spread_runs': 0, 'spread_effective_configs': None},
            {'run': 'aaaa0002', 'config_key': 'a', 'score': 720000, 'passed': True, 'repeat': True,
             'spread': None, 'spread_configs': 0, 'spread_runs': 0, 'spread_effective_configs': None},
            {'run': 'bbbb0001', 'config_key': 'b', 'score': 800000, 'passed': True, 'repeat': False,
             'spread': 0.0199, 'spread_configs': 1, 'spread_runs': 2, 'spread_effective_configs': 1.0},
        ]
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory)
            (bin_dir / 'isuscope').write_text(
                '#!/bin/sh\n[ "$*" = "variance --json" ] || exit 2\ncat <<\'JSON\'\n'
                + json.dumps({'schema_version': 2, 'runs': {'columns': list(rows[0]), 'total_count': len(rows),
                                                           'truncated': False,
                                                           'rows': [list(row.values()) for row in rows]}})
                + '\nJSON\n')
            (bin_dir / 'isuscope').chmod(0o755)
            with mock.patch.dict(os.environ, {'PATH': f'{bin_dir}:{os.environ["PATH"]}'}):
                known = variance.spread(directory)
        self.assertIsNone(known['aaaa0001']['spread'])
        self.assertTrue(known['aaaa0002']['repeat'])
        self.assertEqual(known['aaaa0002']['config'], known['aaaa0001']['config'])
        self.assertEqual(known['bbbb0001'], {'config': 'b', 'repeat': False, 'spread': {'sigma': 0.0199, 'groups': 1, 'runs': 2, 'effective': 1.0}})


if __name__ == '__main__':
    unittest.main()
