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

    def test_reject_unsafe_service_settings(self):
        for key, value in (("container_name", "shared"), ("network_mode", "host"), ("privileged", True),
                           ("ports", [{"host_ip": "0.0.0.0"}]),
                           ("ports", [{"host_ip": "127.0.0.1", "published": "8080"}]),
                           ("volumes", [{"type": "bind", "read_only": False}])):
            config = copy.deepcopy(self.config)
            config["services"]["app"][key] = value
            with self.assertRaises(ValueError, msg=key):
                runtime.validate(config, self.project)
