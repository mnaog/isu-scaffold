import importlib.util
import json
import os
import signal
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

    def test_sigterm_during_db_test_restores_database_and_fails(self):
        script = self.repo / "scripts/ssh-node.sh"
        script.write_text(r'''#!/usr/bin/env python3
import os, pathlib, signal, sys
root = pathlib.Path(os.environ["FAKE_ROOT"])
node, command = sys.argv[1:]
if "cannot identify database" in command:
    print("db\tmariadb" if node == "app1" else "no-db")
elif "systemctl stop" in command:
    (root / "db-stopped").touch()
elif "systemctl restart" in command:
    if node == "app1":
        os.kill(int((root / "parent-pid").read_text()), signal.SIGTERM)
    print("restarted\tapp.service\nISUSCOPE_APP_RESTART_COMPLETE")
elif "systemctl start mariadb" in command:
    (root / "db-restored").touch()
''')
        (self.repo / "config/systemd").mkdir()
        (self.repo / "config/systemd/app.service").write_text("")
        # Write the parent's PID before exec, so the fake SSH can signal the actual command.
        command = ("import os,pathlib,sys; pathlib.Path('.local/parent-pid').write_text(str(os.getpid())); "
                   "os.execv(sys.executable, "
                   "[sys.executable, 'scripts/final-verification.py', 'db-late', '--yes'])")
        result = subprocess.run(["python3", "-c", command], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue((self.repo / ".local/db-stopped").exists(), result.stdout + result.stderr)
        self.assertTrue((self.repo / ".local/db-restored").exists(), result.stdout + result.stderr)
        self.assertIn(f"signal {signal.SIGTERM}", result.stdout)


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


class DbLateTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("final_verification", ROOT / "scripts/final-verification.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.calls = []

    def run_test(self, failure=None, interruption=None):
        module = self.module

        def ssh(node, command, timeout=30):
            self.calls.append((node, command))
            if "systemctl stop" in command:
                stage = "stop"
            elif "systemctl restart" in command:
                stage = "restart"
            elif "systemctl start" in command:
                stage = "restore"
            else:
                self.fail(command)
            if stage == failure:
                return 1, "", "simulated " + stage + " failure"
            if stage == "restart":
                return 0, "restarted\tapp.service\nISUSCOPE_APP_RESTART_COMPLETE\n", ""
            return 0, "", ""

        report = module.Report(quiet=True)
        with patch.object(module, "db_nodes", return_value={"db": "mariadb"}), \
                patch.object(module, "unit_names", return_value=["app.service", "mariadb.service"]), \
                patch.object(module, "ssh", side_effect=ssh), \
                patch.object(module.time, "sleep", side_effect=interruption), \
                patch.object(module, "check_nodes") as checks:
            module.db_late(report, {"http_checks": []}, {}, ["db", "app"])
        return report, checks

    def test_stop_and_restart_failures_still_restore_the_original_database(self):
        for failure in ["stop", "restart", "restore"]:
            with self.subTest(failure=failure):
                self.calls.clear()
                report, checks = self.run_test(failure=failure)
                self.assertTrue(report.failed(), report.lines)
                restores = [(node, cmd) for node, cmd in self.calls if "systemctl start mariadb" in cmd]
                self.assertEqual(len(restores), 1)
                self.assertEqual(restores[0][0], "db")
                checks.assert_not_called()
                if failure == "stop":
                    self.assertFalse(any("systemctl restart" in cmd for _, cmd in self.calls))

    def test_exceptions_and_interrupts_restore_the_database(self):
        for error in [RuntimeError("unexpected failure"), KeyboardInterrupt()]:
            with self.subTest(error=error):
                self.calls.clear()
                report, checks = self.run_test(interruption=error)
                self.assertTrue(report.failed())
                self.assertTrue(any("systemctl start mariadb" in cmd for _, cmd in self.calls))
                checks.assert_not_called()

    def test_success_checks_recovery_after_restoring_database(self):
        report, checks = self.run_test()
        self.assertFalse(report.failed(), report.lines)
        checks.assert_called_once()
        self.assertIn("systemctl start mariadb", self.calls[-1][1])


if __name__ == "__main__":
    unittest.main()
