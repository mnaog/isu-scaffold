import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent.parent


class ApplicationPolicyTests(unittest.TestCase):
    def test_language_mismatch_is_rejected_before_import(self):
        result = subprocess.run(["bash", str(ROOT / "scripts/quick-import-code.sh"), "perl"],
                                env=dict(os.environ, ISUSCOPE_LOCK_HELD="1"), capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"must match config/application.env", result.stderr)


if __name__ == "__main__":
    unittest.main()
