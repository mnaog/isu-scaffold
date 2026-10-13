import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = "ansible/roles/observability/files/vendor"


class BundledToolTests(unittest.TestCase):
    def test_bundled_tools_match_the_pinned_versions(self):
        # A version bump in the defaults without refetching would make bootstrap copy nothing.
        result = subprocess.run([str(ROOT / "scripts/fetch-observability-tools.sh")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_bundled_file_is_exported(self):
        exported = set(json.loads((ROOT / "config/scaffold-export.json").read_text())["files"])
        bundled = {f"{VENDOR}/{line.split()[1]}" for line in (ROOT / VENDOR / "SHA256SUMS").read_text().splitlines()}
        self.assertLessEqual(bundled | {f"{VENDOR}/SHA256SUMS", ".gitattributes"}, exported)


if __name__ == "__main__":
    unittest.main()
