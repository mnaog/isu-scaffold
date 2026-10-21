import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('isuscope_prune', ROOT / 'scripts/isuscope-prune.py')
prune = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prune)


def write(path, text='x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class PruneTest(unittest.TestCase):
    """The per-run caches of earlier isuscope versions go; isuscope's own cache and the saved logs stay."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data = self.root / 'isuscope-data'
        self.ids = [f'01a1598{n}-0000-7000-8000-00000000000{n}' for n in range(3)]
        for run in self.ids:
            logs = self.data / 'runs' / run / 'logs'
            write(logs / 'state-log-delta-app1-after-stdout.zst')
            write(logs / '.state-cache/states.sqlite3')
            write(self.data / 'cache/states' / f'{run}.sqlite3')

    def tearDown(self):
        self.tmp.cleanup()

    def test_removes_the_old_per_run_caches_only(self):
        self.assertEqual(len(prune.prune(self.root)), 3)
        for run in self.ids:
            self.assertFalse((self.data / 'runs' / run / 'logs/.state-cache').exists())
            self.assertTrue((self.data / 'runs' / run / 'logs/state-log-delta-app1-after-stdout.zst').exists())
            self.assertTrue((self.data / 'cache/states' / f'{run}.sqlite3').exists())
        self.assertEqual(prune.prune(self.root), [])

    def test_reads_data_dir_from_the_isuscope_config(self):
        write(self.root / '.isuscope/config.toml', 'data_dir = "elsewhere"\n')
        self.assertEqual(prune.data_dir(self.root), self.root / 'elsewhere')
        self.assertEqual(prune.prune(self.root), [])  # no runs there

    def test_marks_the_data_dir_for_backups_once(self):
        first = prune.exclude_from_backups(self.root)
        if first:  # macOS
            self.assertTrue((self.data / '.metadata_never_index').exists())
        self.assertFalse(prune.exclude_from_backups(self.root))


if __name__ == '__main__':
    unittest.main()
