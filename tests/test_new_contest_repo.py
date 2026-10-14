import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/new-contest-repo.py"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


class NewContestRepoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # A small committed scaffold: the script copies the tree of a commit, never the work tree.
        self.source = self.tmp / "scaffold"
        (self.source / "scripts").mkdir(parents=True)
        (self.source / "config/preparation").mkdir(parents=True)
        for name in ("new-contest-repo.py", "preparation-check.py"):
            (self.source / "scripts" / name).write_bytes((ROOT / "scripts" / name).read_bytes())
            (self.source / "scripts" / name).chmod(0o755)
        (self.source / "config/new-repo.json").write_bytes((ROOT / "config/new-repo.json").read_bytes())
        (self.source / "config/preparation/readiness.md").write_text("from {{SOURCE}} at {{CREATED_AT}} as {{MODE}}\n")
        (self.source / "AGENTS.md").write_text(
            "# Repository instructions\n\n## 練習時のみ\n練習中は[practice.md](practice.md)を読む。\n\n## ディレクトリ\n")
        (self.source / "practice.md").write_text("practice\n")
        (self.source / ".claude/rules").mkdir(parents=True)
        os.symlink("../../AGENTS.md", self.source / ".claude/rules/agents.md")
        for name in ("docs/agent-history/Readme.md", "docs/agent-history/20261013-123404.md",
                     "docs/official/lithosai-20261001/opencode.md", "docs/official/README.md",
                     "docs/ai-compare/Readme.md", "isuscope-data/README.md", "isuscope-data/runs/x.json"):
            (self.source / name).parent.mkdir(parents=True, exist_ok=True)
            (self.source / name).write_text(name + "\n")
        git(self.source.parent, "init", "-q", "-b", "main", str(self.source))
        git(self.source, "add", "-A")
        git(self.source, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "scaffold")

    def create(self, *args):
        target = self.tmp / "contest"
        environment = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        completed = subprocess.run(["python3", str(self.source / "scripts/new-contest-repo.py"), str(target), *args],
                                   capture_output=True, text=True, env=environment)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return target

    def test_contest_copy_leaves_out_the_scaffold_history_and_practice(self):
        target = self.create()
        files = set(git(target, "ls-files").split())
        self.assertIn("docs/agent-history/Readme.md", files)
        self.assertIn("docs/official/README.md", files)
        self.assertIn("isuscope-data/README.md", files)
        for name in ("docs/agent-history/20261013-123404.md", "docs/official/lithosai-20261001/opencode.md",
                     "docs/ai-compare/Readme.md", "isuscope-data/runs/x.json", "practice.md",
                     "config/new-repo.json", "config/preparation/readiness.md", "scripts/new-contest-repo.py"):
            self.assertNotIn(name, files)
        self.assertEqual((target / "AGENTS.md").read_text(), "# Repository instructions\n\n## ディレクトリ\n")
        self.assertTrue((target / ".claude/rules/agents.md").is_symlink())
        self.assertEqual(json.loads((target / "docs/preparation/scaffold-source.json").read_text())["commit"],
                         git(self.source, "rev-parse", "HEAD").strip())
        self.assertIn("as 本番", (target / "docs/preparation/readiness.md").read_text())
        self.assertEqual(git(target, "status", "--porcelain"), "")
        self.assertEqual(len(git(target, "log", "--oneline").splitlines()), 1)

    def test_practice_copy_keeps_the_practice_instructions(self):
        target = self.create("--practice")
        self.assertTrue((target / "practice.md").exists())
        self.assertIn("## 練習時のみ", (target / "AGENTS.md").read_text())
        self.assertIn("as 練習", (target / "docs/preparation/readiness.md").read_text())

    def test_refuses_a_non_empty_target(self):
        (self.tmp / "contest").mkdir()
        (self.tmp / "contest/file").write_text("x")
        completed = subprocess.run(["python3", str(self.source / "scripts/new-contest-repo.py"), str(self.tmp / "contest")],
                                   capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
