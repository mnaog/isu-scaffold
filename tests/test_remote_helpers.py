import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Helpers that Ansible copies to targets must run on the oldest target Python the legacy controller supports.
REMOTE_HELPERS = ["scripts/tree-digest.py"]


class RemoteHelperTests(unittest.TestCase):
    def test_helpers_run_on_oldest_target_python(self):
        python = shutil.which("python3.8")
        if not python:
            self.skipTest("python3.8 is not installed")
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "file").write_text("content")
            for helper in REMOTE_HELPERS:
                with self.subTest(helper=helper):
                    result = subprocess.run([python, str(ROOT / helper), directory],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    current = subprocess.run(["python3", str(ROOT / helper), directory],
                                             capture_output=True, text=True, check=True)
                    self.assertEqual(result.stdout, current.stdout)


if __name__ == "__main__":
    unittest.main()
