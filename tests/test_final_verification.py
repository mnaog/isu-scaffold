import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
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
elif "cannot identify database" in command:
    print("db\tmysql" if node == "app1" else "no-db")
elif "GROUP_CONCAT" in command:
    counted = root / (node + ".counted")
    rows = 9 if counted.exists() else 10
    counted.write_text("1")
    print("isuride.rides\t%d\nisuride.users\t3" % rows)
    print("ISUSCOPE_TABLE_COUNTS_COMPLETE")
elif "say()" in command:
    print("OK\tunit app.service\tenabled, active")
    print("ISUSCOPE_FINAL_CHECK_COMPLETE")
'''


class FinalVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        for folder in ("scripts", "config", ".local"):
            (self.repo / folder).mkdir()
        shutil.copy2(ROOT / "scripts/final-verification.py", self.repo / "scripts/final-verification.py")
        shutil.copy2(ROOT / "scripts/isuscope_sql.py", self.repo / "scripts/isuscope_sql.py")
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

    def test_failed_snapshot_prevents_reboot(self):
        script = self.repo / "scripts/ssh-node.sh"
        script.write_text(FAKE_SSH.replace('counted = root / (node + ".counted")',
                                         'sys.exit(1)\n    counted = root / (node + ".counted")'))
        result = self.run_script("reboot", "--yes")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("table counts", result.stdout)
        self.assertFalse((self.repo / ".local/final-snapshot.json").exists())
        self.assertEqual((self.repo / ".local/app1.boot").read_text(), "0")


class UnitAndScoreTests(unittest.TestCase):
    def load(self):
        spec = importlib.util.spec_from_file_location("final_verification", ROOT / "scripts/final-verification.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_generated_remote_shell_syntax(self):
        module = self.load()
        config = {"admin_users": ["isucon"], "protected_units": ["envcheck.service"],
                  "envcheck_command": "true"}
        scripts = [module.COUNT_SCRIPT, module.node_script(
            config, ["app.service"], [{"url": "http://127.0.0.1/"}], envcheck=True)]
        for script in scripts:
            with self.subTest(script=script):
                result = subprocess.run(["sh", "-n"], input=script, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

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

    def test_the_reproduction_check_uses_the_worst_run_of_the_final_configuration(self):
        module = self.load()
        # The other configuration scored more, the final one's worst run is 820 of the last 1000.
        runs = [{"id": f"01a15900-0000-7000-8000-00000000000{n}", "score": score, "passed": 1, "config_key": key}
                for n, (score, key) in enumerate([(2000, "other"), (990, "final"), (820, "final"), (1000, "final")])]
        with patch.object(module, "final_runs", return_value=runs):
            report = module.Report(quiet=True)
            module.check_score(report, {"min_score_ratio": 0.85})
        lines = {name: (level, detail) for level, _, name, detail in report.lines}
        self.assertEqual(lines["last run"][0], "OK")
        self.assertEqual(lines["reproduction"][0], "FAIL")
        self.assertIn("worst 820 is 82% of the last 1000 over 3 runs", lines["reproduction"][1])

    def test_final_runs_ask_isuscope_for_runs_whose_benchmark_started(self):
        module = self.load()
        with patch.object(module.isuscope_sql, "rows", return_value=[]) as rows:
            module.final_runs(Path("/repo"))
        query = rows.call_args.args[1]
        self.assertIn("benchmark_started = 1", query)
        self.assertIn("config_key", query)

    def test_node_checks_reject_errors_empty_and_partial_output(self):
        module = self.load()
        row = "OK\tunit app.service\tenabled, active\n"
        complete = "ISUSCOPE_FINAL_CHECK_COMPLETE\n"
        for result in [(1, "", "failed"), (255, row + complete, "connection lost"),
                       (None, "", "timeout"), (0, "", ""), (0, row, ""),
                       (0, complete, ""), (0, "unrecognized output\n", "")]:
            with self.subTest(result=result), patch.object(module, "ssh", return_value=result):
                report = module.Report(quiet=True)
                module.check_nodes(report, {"http_checks": []}, {}, ["app"])
                self.assertTrue(report.failed(), report.lines)
        with patch.object(module, "ssh", return_value=(0, row + complete, "")):
            report = module.Report(quiet=True)
            module.check_nodes(report, {"http_checks": []}, {}, ["app"])
            self.assertFalse(report.failed(), report.lines)

    def test_row_counts_reject_failure_partial_and_malformed_output(self):
        module = self.load()
        complete = "ISUSCOPE_TABLE_COUNTS_COMPLETE\n"
        for result in [(1, "", "denied"), (None, "", "timeout"), (0, "", ""),
                       (0, "app.rows\t3\n", ""), (0, "app.rows\tbad\n" + complete, ""),
                       (0, complete, ""), (1, "app.rows\t3\n" + complete, "failed")]:
            with self.subTest(result=result), patch.object(module, "ssh", return_value=result):
                report = module.Report(quiet=True)
                counts = module.table_counts(report, ["db"])
                self.assertEqual(counts, {"db": None})
                self.assertTrue(report.failed())
        with patch.object(module, "ssh", return_value=(
                0, "ISUSCOPE_TABLE_COUNTS_EMPTY\n" + complete, "")):
            report = module.Report(quiet=True)
            self.assertEqual(module.table_counts(report, ["db"]), {"db": {}})
            self.assertFalse(report.failed())

    def test_missing_counts_and_missing_empty_table_cannot_pass_comparison(self):
        module = self.load()
        for before, after in [({"db": None}, {"db": {}}), ({"db": {}}, {"db": None}),
                              ({"db": {"empty": 0}}, {"db": {}})]:
            with self.subTest(before=before, after=after):
                report = module.Report(quiet=True)
                module.compare_counts(report, before, after)
                self.assertTrue(report.failed())

    def test_db_discovery_distinguishes_absence_from_failure(self):
        module = self.load()
        for result, failed in [((0, "no-db\n", ""), False), ((0, "", ""), True),
                               ((1, "", "failed"), True), ((None, "", "timeout"), True)]:
            with self.subTest(result=result), patch.object(module, "ssh", return_value=result):
                report = module.Report(quiet=True)
                self.assertEqual(module.db_nodes(report, ["app"]), {})
                self.assertEqual(report.failed(), failed)


if __name__ == "__main__":
    unittest.main()
