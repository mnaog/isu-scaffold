import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("runtime", ROOT / "scripts/local-runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class LocalRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.project = runtime.project_name(ROOT)
        self.config = {"services": {"app": {"ports": [{"host_ip": "127.0.0.1", "published": "0"}],
                                           "volumes": [{"type": "bind", "read_only": True}]}},
                       "volumes": {"db": {"name": self.project + "_db"}}}

    def test_worktrees_are_isolated_and_names_stable(self):
        self.assertEqual(runtime.project_name(ROOT), self.project)
        self.assertNotEqual(runtime.project_name(ROOT.parent / "another-worktree"), self.project)
        runtime.validate(self.config, self.project)

    def test_reject_shared_and_external_resources(self):
        for value in ({"external": True}, {"name": "shared-db"}):
            config = copy.deepcopy(self.config)
            config["volumes"]["db"] = value
            with self.assertRaises(ValueError):
                runtime.validate(config, self.project)

    def test_up_keeps_image_dependencies_and_rebuilds_the_rest(self):
        config = {"services": {"db": {"image": "mysql"}, "cache": {"image": "memcached"},
                               "app": {"build": {}, "depends_on": {"db": {}, "cache": {}}},
                               "nginx": {"image": "nginx", "depends_on": {"app": {}}},
                               "tool": {"image": "busybox"}}}
        self.assertEqual(runtime.split_services(config), (["db", "cache"], ["app", "nginx", "tool"]))

    def test_state_log_is_looked_for_where_each_service_writes_it(self):
        config = {"services": {"api": {"environment": {"ISUCON_DB_HOST": "db"}},
                               "worker": {"environment": {"ISUSCOPE_STATE_LOG": "/tmp/state.jsonl"}},
                               "quiet": {"environment": {"ISUSCOPE_STATE_LOG": ""}},
                               "db": {}}}
        self.assertEqual(list(runtime.state_log_sources(config)),
                         [("api", runtime.DEFAULT_STATE_LOG), ("worker", "/tmp/state.jsonl"),
                          ("db", runtime.DEFAULT_STATE_LOG)])

    def test_race_reports_are_cut_from_the_app_log(self):
        log = "\n".join([
            "local app built with -race",
            "listening on :8080",
            "==================",
            "WARNING: DATA RACE",
            "Write at 0x00c000 by goroutine 8:",
            "  main.(*store).put()",
            "==================",
            "ok",
            "==================",
            "WARNING: DATA RACE",
            "Read at 0x00c010 by goroutine 9:",
        ])
        reports = runtime.race_reports(log)
        self.assertEqual(len(reports), 2)
        self.assertIn("main.(*store).put()", reports[0])
        self.assertNotIn("ok", reports[0])
        self.assertIn("Read at", reports[1], "a report cut off at the end of the log still counts")
        self.assertEqual(runtime.race_reports("listening\n"), [])

    def test_the_example_compose_passes_the_race_switch_to_the_app(self):
        example = (ROOT / "config/local/compose.example.yaml").read_text()
        self.assertIn("LOCAL_RACE: ${LOCAL_RACE:-}", example)
        start = (ROOT / "config/local/start.sh").read_text()
        self.assertIn("-race", start)
        self.assertIn(runtime.RACE_BUILT, start)

    def test_reject_unsafe_service_settings(self):
        for key, value in (("container_name", "shared"), ("network_mode", "host"), ("privileged", True),
                           ("ports", [{"host_ip": "0.0.0.0"}]),
                           ("ports", [{"host_ip": "127.0.0.1", "published": "8080"}]),
                           ("volumes", [{"type": "bind", "read_only": False}])):
            config = copy.deepcopy(self.config)
            config["services"]["app"][key] = value
            with self.assertRaises(ValueError, msg=key):
                runtime.validate(config, self.project)
