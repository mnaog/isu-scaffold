import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# boot_idはrebootのたびに変わる。行数は2回目の集計でapp1のridesが1行減る。app2は戻ってこない。
FAKE_SSH = r'''#!/usr/bin/env python3
import os, pathlib, sys
root = pathlib.Path(os.environ["FAKE_ROOT"])
node, command = sys.argv[1:]
state = root / (node + ".boot")
if not state.exists():
    state.write_text("0")
if "systemctl reboot" in command:
    state.write_text(str(int(state.read_text()) + 1))
    sys.exit(255)
if "boot_id" in command:
    if node == "app2" and state.read_text() != "0":
        sys.exit(255)
    print("boot-" + state.read_text())
elif "is-active -q mysql" in command and "say()" not in command:
    print("db" if node == "app1" else "")
elif "GROUP_CONCAT" in command:
    counted = root / (node + ".counted")
    rows = 9 if counted.exists() else 10
    counted.write_text("1")
    print("isuride.rides\t%d\nisuride.users\t3" % rows)
elif "say()" in command:
    print("OK\tunit app.service\tenabled, active")
'''


class FinalVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        for folder in ("scripts", "config", ".local"):
            (self.repo / folder).mkdir()
        shutil.copy2(ROOT / "scripts/final-verification.py", self.repo / "scripts/final-verification.py")
        (self.repo / "scripts/ssh-node.sh").write_text(FAKE_SSH)
        (self.repo / "scripts/ssh-node.sh").chmod(0o755)
        (self.repo / ".local/ansible-inventory.json").write_text(json.dumps(
            {"all": {"children": {"application": {"hosts": {"app1": {}, "app2": {}}}}}}))
        (self.repo / "config/final-check.json").write_text(json.dumps(
            {"http_checks": [], "reboot_timeout_seconds": 12, "reboot_grace_seconds": 0, "settle_seconds": 0}))
        self.env = dict(os.environ, FAKE_ROOT=str(self.repo / ".local"), ISUSCOPE_LOCK_HELD="1")
        self.env.pop("ANSIBLE_INVENTORY", None)

    def run_script(self, *args):
        return subprocess.run(["python3", "scripts/final-verification.py", *args], cwd=self.repo, env=self.env,
                              capture_output=True, text=True, timeout=60)

    def test_reboot_needs_yes(self):
        result = self.run_script("reboot")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--yes", result.stderr)

    def test_reboot_reports_a_node_that_does_not_return_and_lost_rows(self):
        result = self.run_script("reboot", "--yes")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"OK\s+app1\s+rebooted")
        self.assertRegex(result.stdout, r"FAIL\s+app2\s+rebooted: did not come back within 12s")
        self.assertIn("rows lost: isuride.rides 10->9", result.stdout)
        snapshot = json.loads((self.repo / ".local/final-snapshot.json").read_text())
        self.assertEqual(snapshot["counts"]["app1"]["isuride.rides"], 10)


class UnitAndScoreTests(unittest.TestCase):
    def load(self):
        spec = importlib.util.spec_from_file_location("final_verification", ROOT / "scripts/final-verification.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_units_come_from_sync_systemd_and_middleware(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config/systemd").mkdir(parents=True)
            (root / "config/systemd/isu.service").write_text("")
            (root / "config/systemd/isu-sweep.timer").write_text("")
            (root / "config/sync.json").write_text(json.dumps({
                "status_commands": ["systemctl is-active isu-matcher"],
                "post_deploy_commands": [{"node_group": "role_app", "command": "sudo systemctl enable --now isu-edge isu-worker"},
                                         {"command": "sudo systemctl daemon-reload"}]}))
            module.ROOT = root
            units = module.unit_names({"units": ["extra"], "protected_units": ["envcheck.service"]})
        for unit in ["extra.service", "isu-matcher.service", "isu-edge.service", "isu-worker.service",
                     "isu.service", "isu-sweep.timer", "nginx.service", "mysql.service", "envcheck.service"]:
            self.assertIn(unit, units)
        self.assertNotIn("daemon-reload.service", units)

    def test_the_reproduction_check_uses_the_worst_run_at_head(self):
        module = self.load()
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        with tempfile.TemporaryDirectory() as directory:
            fake = Path(directory) / "isuscope"
            rows = [[head, 1000, 1], [head, 820, 1], [head, 990, 1], ["other", 2000, 1]]
            fake.write_text("#!/bin/sh\nprintf '%s\\n' '" + json.dumps({"rows": rows}) + "'\n")
            fake.chmod(0o755)
            old = os.environ["PATH"]
            os.environ["PATH"] = f"{directory}{os.pathsep}{old}"
            try:
                report = module.Report(quiet=True)
                module.check_score(report, {"min_score_ratio": 0.85})
            finally:
                os.environ["PATH"] = old
        lines = {name: (level, detail) for level, _, name, detail in report.lines}
        self.assertEqual(lines["last run"][0], "OK")
        self.assertEqual(lines["reproduction"][0], "FAIL")
        self.assertIn("worst 820 is 82% of the last 1000", lines["reproduction"][1])


if __name__ == "__main__":
    unittest.main()
