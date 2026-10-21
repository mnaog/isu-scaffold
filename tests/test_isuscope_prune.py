import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('isuscope_prune', ROOT / 'scripts/isuscope-prune.py')
prune = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prune)


def write(path, text='x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class PruneTest(unittest.TestCase):
    """Old state-log caches go; the latest runs' caches, recent ones and the saved logs stay."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / 'isuscope-data'
        self.ids = [f'01a1598{n}-0000-7000-8000-00000000000{n}' for n in range(5)]
        old = time.time() - 3600
        for run in self.ids:
            logs = self.data / 'runs' / run / 'logs'
            write(logs / 'state-log-delta-app1-after-stdout.zst')
            write(logs / '.state-cache/states.sqlite3')
            write(self.data / 'cache/states' / f'{run}.sqlite3')
            for path in [logs / '.state-cache/states.sqlite3', logs / '.state-cache',
                         self.data / 'cache/states' / f'{run}.sqlite3']:
                os.utime(path, (old, old))
        write(self.data / 'cache/states/.gitignore', '*\n')

    def tearDown(self):
        self.tmp.cleanup()

    def cache(self, run):
        return self.data / 'runs' / run / 'logs/.state-cache'

    def test_keeps_the_caches_of_the_latest_runs_and_every_saved_log(self):
        removed = prune.prune(self.root, keep=3)
        self.assertEqual(len(removed), 4)
        for run in self.ids[:2]:
            self.assertFalse(self.cache(run).exists())
            self.assertFalse((self.data / 'cache/states' / f'{run}.sqlite3').exists())
        for run in self.ids[2:]:
            self.assertTrue(self.cache(run).exists())
            self.assertTrue((self.data / 'cache/states' / f'{run}.sqlite3').exists())
        for run in self.ids:
            self.assertTrue((self.data / 'runs' / run / 'logs/state-log-delta-app1-after-stdout.zst').exists())
        self.assertTrue((self.data / 'cache/states/.gitignore').exists())
        self.assertEqual(prune.prune(self.root, keep=3), [])

    def test_a_cache_in_use_stays(self):
        os.utime(self.cache(self.ids[0]) / 'states.sqlite3')  # a query just read or built it
        prune.prune(self.root, keep=3)
        self.assertTrue(self.cache(self.ids[0]).exists())
        self.assertFalse(self.cache(self.ids[1]).exists())

    def test_reads_data_dir_from_the_isuscope_config(self):
        write(self.root / '.isuscope/config.toml', 'data_dir = "elsewhere"\n')
        self.assertEqual(prune.data_dir(self.root), self.root / 'elsewhere')
        self.assertEqual(prune.prune(self.root), [])  # no runs there
        self.assertTrue(self.cache(self.ids[0]).exists())

    def test_marks_the_data_dir_for_backups_once(self):
        first = prune.exclude_from_backups(self.root)
        if first:  # macOS
            self.assertTrue((self.data / '.metadata_never_index').exists())
        self.assertFalse(prune.exclude_from_backups(self.root))


if __name__ == '__main__':
    unittest.main()
