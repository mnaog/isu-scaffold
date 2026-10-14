import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class EnvironmentReportTests(unittest.TestCase):
    """docs/environment.md comes from kickoff's own data, and hand edits are never overwritten."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / 'scripts').mkdir()
        for name in ('environment-report.py', 'progress.py'):
            shutil.copy(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        local = self.repo / '.local'
        (local / 'inspection').mkdir(parents=True)
        (local / 'initial-build').mkdir()
        (local / 'ansible-inventory.json').write_text(json.dumps({'all': {'children': {
            'application': {'hosts': {'app1': {}, 'app2': {}}}, 'benchmark': {'hosts': {'bench': {}}}}}}))
        for node in ('app1', 'app2'):
            (local / 'inspection' / f'{node}.json').write_text(json.dumps({
                'node': node, 'distribution': 'Ubuntu', 'distribution_version': '24.04', 'kernel': '6.8.0',
                'architecture': 'x86_64', 'processor_vcpus': 2, 'memory_mb': 3900,
                'versions': {'nginx': 'nginx version: nginx/1.24.0', 'mysql': 'mysql  Ver 8.0.36', 'go': ''}}))
        (local / 'initial-build/environment.txt').write_text(
            'x86_64\nPRETTY_NAME="Ubuntu 24.04 LTS"\nglibc 2.39\n/home/isucon/.cargo/bin/rustc\nrustc 1.80.0\ncargo 1.80.0\n')
        (local / 'progress.log').write_text(
            '2026-10-13T10:00:00+0900\tkickoff\tstarted\tmake kickoff\n'
            '2026-10-13T10:00:14+0900\tkickoff\tcode-ready\twebapp/rust\n'
            '2026-10-13T10:01:48+0900\tkickoff\tdraft-ready\tread review.md\n')

    def tearDown(self):
        self.tmp.cleanup()

    def run_report(self):
        env = {k: v for k, v in os.environ.items() if k != 'PROGRESS_LOG'}
        return subprocess.run(['python3', 'scripts/environment-report.py'], cwd=self.repo, env=env,
                              capture_output=True, text=True)

    def test_report_from_inventory_inspection_build_probe_and_progress(self):
        result = self.run_report()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (self.repo / 'docs/environment.md').read_text()
        self.assertIn('3台', text)
        self.assertIn('| bench | benchmark |', text)
        self.assertIn('| app1 | Ubuntu 24.04 | 6.8.0 | x86_64 | 2 | 3900 MB |', text)
        self.assertIn('nginx/1.24.0', text)
        self.assertNotIn('| go |', text)  # tools found nowhere get no column
        self.assertIn('rustc 1.80.0', text)
        self.assertIn('| 10:00:14 | 0:14 | kickoff | code-ready |', text)
        self.assertIn('| 10:01:48 | 1:48 | kickoff | draft-ready |', text)

    def test_hand_edited_report_is_kept(self):
        self.run_report()
        target = self.repo / 'docs/environment.md'
        target.write_text('# 環境\n\n手で直した記録\n')
        self.assertEqual(self.run_report().returncode, 0)
        self.assertEqual(target.read_text(), '# 環境\n\n手で直した記録\n')
        self.assertIn('kickoff', (self.repo / '.local/environment-report.md').read_text())


if __name__ == '__main__':
    unittest.main()
