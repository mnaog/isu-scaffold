import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent


class RecordsTest(unittest.TestCase):
    """config/records.json and the phase documents name the same fixed places."""

    def test_every_record_is_named_where_the_phases_ask_for_it(self):
        records = json.loads((ROOT / 'config/records.json').read_text())['records']
        documents = ''.join(path.read_text() for path in sorted((ROOT / 'docs/phases').glob('*.md')))
        paths = [record['path'] for record in records]
        self.assertEqual(len(paths), len(set(paths)))
        for record in records:
            self.assertTrue(record['title'] and record['phase'] and record['contents'], record)
            self.assertIn(f"`{record['path']}`", documents, record['path'])


if __name__ == '__main__':
    unittest.main()
