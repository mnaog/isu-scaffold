import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent


class RecordsTest(unittest.TestCase):
    """config/records.json and the guides name the same fixed places."""

    def test_every_record_is_named_where_the_guides_ask_for_it(self):
        records = json.loads((ROOT / 'config/records.json').read_text())['records']
        guides = ('preparation.md', 'kickoff.md', 'finish.md')
        documents = ''.join((ROOT / 'docs/guides' / name).read_text() for name in guides)
        paths = [record['path'] for record in records]
        self.assertEqual(len(paths), len(set(paths)))
        for record in records:
            self.assertTrue(record['title'] and record['timing'] and record['contents'], record)
            self.assertIn(f"`{record['path']}`", documents, record['path'])


if __name__ == '__main__':
    unittest.main()
