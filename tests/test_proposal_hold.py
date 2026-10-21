import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProposalHoldTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("proposal", ROOT / "scripts/proposal.py")
        self.proposal = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.proposal)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.env = dict(os.environ, GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="test@example.com",
                        GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="test@example.com")
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "base")
        self.git("switch", "-q", "-c", "idea")
        self.git("commit", "-q", "--allow-empty", "-m", "idea")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.git("switch", "-q", "main")
        self.data = {"branch": "idea", "commit": self.commit, "status": "submitted"}
        self.proposal.save(self.repo, self.data)

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, env=self.env, text=True)

    def test_unmerged_proposal_can_be_held(self):
        self.proposal.hold(self.repo, "idea", "CPU becomes the bottleneck")
        self.assertEqual(self.proposal.load(self.repo, "idea")["status"], "held")
        self.assertEqual(self.proposal.queue_count(self.repo), 0)

    def test_hold_refuses_merged_code_even_before_status_refresh(self):
        self.git("merge", "-q", "--no-ff", "-m", "merge idea", "idea")
        for status in ("submitted", "delivered", "needs-sync", "merged", "measuring"):
            with self.subTest(status=status):
                self.proposal.save(self.repo, dict(self.data, status=status))
                with self.assertRaisesRegex(self.proposal.Refused, "取り込み済み"):
                    self.proposal.hold(self.repo, "idea", "later")
                self.assertNotEqual(self.proposal.load(self.repo, "idea")["status"], "held")
        self.proposal.save(self.repo, dict(self.data, status="merged"))
        self.assertEqual(self.proposal.start_measuring(self.repo), ["idea"])
