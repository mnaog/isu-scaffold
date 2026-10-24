from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

WRITER = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("proposal", sys.argv[1])
proposal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proposal)
for i in range(300):
    proposal.write_json(sys.argv[2], {"branch": "idea", "writer": sys.argv[3], "i": i})
"""


class ProposalWriteTests(unittest.TestCase):
    def test_two_processes_can_write_the_same_proposal_at_once(self):
        # The benchmark's hypothesis and the background sync both rewrite a proposal's file.
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "proposals" / "idea.json"
            writers = [subprocess.Popen([sys.executable, "-c", WRITER, str(ROOT / "scripts/proposal.py"), str(target), name],
                                        stderr=subprocess.PIPE, text=True) for name in ("a", "b")]
            for writer in writers:
                _, err = writer.communicate(timeout=60)
                self.assertEqual(writer.returncode, 0, err)
            self.assertEqual([p.name for p in target.parent.iterdir()], ["idea.json"])


if __name__ == "__main__":
    unittest.main()
