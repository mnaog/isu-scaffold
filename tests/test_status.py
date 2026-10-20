import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# app1 runs the remote script in a local shell, app2 hangs, app3 cannot connect.
FAKE_SSH = '''#!/usr/bin/env python3
import os, pathlib, subprocess, sys, time
node, command = sys.argv[1:]
pathlib.Path(os.environ["FAKE_ROOT"], node + ".command").write_text(command)
if node == "app2":
    time.sleep(60)
if node == "app3":
    print("Connection timed out during banner exchange", file=sys.stderr)
    sys.exit(255)
sys.exit(subprocess.run(["sh", "-c", command]).returncode)
'''


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        for folder in ("scripts", "config", ".local"):
            (self.repo / folder).mkdir()
        for script in ("status.sh", "sync-lib.sh", "check-deployment.py", "local-build.py"):
            shutil.copy2(ROOT / "scripts" / script, self.repo / "scripts" / script)
        (self.repo / "scripts/ssh-node.sh").write_text(FAKE_SSH)
        (self.repo / "scripts/ssh-node.sh").chmod(0o755)
        self.env = dict(os.environ, FAKE_ROOT=str(self.repo / ".local"), STATUS_TIMEOUT_SECONDS="2")
        self.env.pop("SYNC_MANIFEST", None)
        self.env.pop("ANSIBLE_INVENTORY", None)

    def write(self, nodes, status_commands):
        (self.repo / ".local/ansible-inventory.json").write_text(json.dumps(
            {"all": {"children": {"application": {"hosts": {node: {} for node in nodes}},
                                  "role_db": {"hosts": {"app1": {}}}}}}))
        (self.repo / "config/sync.json").write_text(json.dumps({
            "source_node": "app1", "pre_deploy_command": "",
            "items": [{"name": "app", "type": "directory", "node_group": "application",
                       "local": "webapp", "remote": "/home/isucon/webapp",
                       "owner": "isucon", "owner_group": "isucon"}],
            "post_deploy_commands": [], "status_commands": status_commands}))

    def status(self):
        started = time.monotonic()
        result = subprocess.run(["bash", "scripts/status.sh"], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, timeout=30)
        return result, time.monotonic() - started

    def test_every_node_answers_or_is_reported_within_the_limit(self):
        self.write(["app1", "app2", "app3"],
                   ["echo app-is-active", {"node_group": "role_db", "command": "echo 'db is up'"}])
        result, elapsed = self.status()
        # The hanging node is cut at the limit; the nodes are inspected in parallel.
        self.assertLess(elapsed, 8, result.stdout)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("== app1: ok", result.stdout)
        self.assertIn("check    ok      echo app-is-active: app-is-active", result.stdout)
        self.assertIn("check    ok      echo 'db is up': db is up", result.stdout)
        self.assertIn("load     ", result.stdout)
        self.assertIn("== app2: no answer within 2s", result.stdout)
        self.assertIn("== app3: SSH failed", result.stdout)
        self.assertIn("banner exchange", result.stdout)
        # Group commands go only to the nodes of that group, in the same one SSH session.
        self.assertNotIn("db is up", (self.repo / ".local/app3.command").read_text())

    def test_a_failing_status_command_fails_the_status(self):
        self.write(["app1"], ["false", "echo fine"])
        result, _ = self.status()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("== app1: a status command failed", result.stdout)
        self.assertIn("check    FAILED  false (exit 1)", result.stdout)
        self.assertIn("check    ok      echo fine", result.stdout)

        self.write(["app1"], [])
        result, _ = self.status()
        self.assertEqual(result.returncode, 0, result.stdout)


if __name__ == "__main__":
    unittest.main()
