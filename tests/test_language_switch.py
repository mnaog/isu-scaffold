import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


def draft_module():
    spec = importlib.util.spec_from_file_location('draft_switch', ROOT / 'scripts/configure-draft.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LanguageSwitchDraftTest(unittest.TestCase):
    """practice-14 started with isuride-go running; the draft FAILed until the units were switched by hand."""

    def setUp(self):
        self.draft = draft_module()
        self.units = {'isuride-go.service', 'isuride-rust.service', 'isuride-python.service',
                      'isuride-matcher.service', 'isuride-payment_mock.service', 'nginx.service'}

    def test_switches_only_where_another_language_runs(self):
        reports = {
            'app1': {'running_services': ['isuride-go.service', 'isuride-matcher.service', 'nginx']},
            'app2': {'running_services': ['isuride-go.service', 'isuride-payment_mock.service']},
            'app3': {'running_services': ['isuride-rust.service']},
        }
        switch = self.draft.language_switch('isuride-rust.service', reports, self.units)
        self.assertEqual(switch, {'start': 'isuride-rust.service', 'stop': ['isuride-go.service'],
                                  'nodes': ['app1', 'app2']})

    def test_no_switch_when_rust_already_runs_alone_or_nothing_runs(self):
        self.assertIsNone(self.draft.language_switch(
            'isuride-rust.service', {'app1': {'running_services': ['isuride-rust.service']}}, self.units))
        self.assertIsNone(self.draft.language_switch(
            'isuride-rust.service', {'app1': {'running_services': ['nginx.service']}}, self.units))

    def test_dot_separated_units_and_unrelated_prefixes(self):
        units = {'isucondition.go.service', 'isucondition.rust.service', 'other-go.service'}
        reports = {'app1': {'running_services': ['isucondition.go.service', 'other-go.service']}}
        switch = self.draft.language_switch('isucondition.rust.service', reports, units)
        self.assertEqual(switch['stop'], ['isucondition.go.service'])

    def test_nginx_site_items_use_real_paths_and_skip_the_measurement_dropin(self):
        report = {'nginx_site_files': [
            '/etc/nginx/sites-enabled/isuride.conf\t/etc/nginx/sites-available/isuride.conf',
            '/etc/nginx/sites-enabled/default\t/etc/nginx/sites-available/default',
            '/etc/nginx/conf.d/00-isuscope-log.conf\t/etc/nginx/conf.d/00-isuscope-log.conf',
            '/etc/nginx/conf.d/upstream.conf\t/etc/nginx/conf.d/upstream.conf',
            '/etc/nginx/sites-enabled/app\t/home/isucon/etc/nginx/app.conf',
        ]}
        items, warnings = self.draft.nginx_site_items(report, 'app1')
        by_name = {item['name']: item for item in items}
        self.assertEqual(by_name['nginx-site-isuride']['local'], 'config/nginx/sites-available/isuride.conf')
        self.assertEqual(by_name['nginx-site-isuride']['remote'], '/etc/nginx/sites-available/isuride.conf')
        self.assertEqual(by_name['nginx-confd-upstream']['local'], 'config/nginx/conf.d/upstream.conf')
        self.assertNotIn('nginx-confd-00-isuscope-log', by_name)
        self.assertEqual(by_name['nginx-file-app']['remote'], '/home/isucon/etc/nginx/app.conf')
        self.assertTrue(any('outside /etc/nginx' in warning for warning in warnings))
        self.assertTrue(all(item['source_node'] == 'app1' and item['node_group'] == 'role_nginx' for item in items))


class SwitchScriptTest(unittest.TestCase):
    def run_switch(self, plan):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            (root / '.local').mkdir()
            shutil.copy(ROOT / 'scripts/switch-language-service.sh', root / 'scripts/switch-language-service.sh')
            log = root / 'ssh.log'
            fake = root / 'scripts/ssh-node.sh'
            fake.write_text('#!/usr/bin/env bash\nprintf "%s\\t%s\\n" "$1" "$2" >>"' + str(log) + '"\n')
            fake.chmod(0o755)
            if plan is not None:
                (root / '.local/service-switch.json').write_text(json.dumps(plan))
            result = subprocess.run([str(root / 'scripts/switch-language-service.sh')], capture_output=True, text=True,
                                    env={**os.environ, 'ISUSCOPE_LOCK_HELD': '1'})
            calls = log.read_text().splitlines() if log.exists() else []
            return result, calls

    def test_stops_the_other_language_and_starts_rust_on_listed_nodes(self):
        result, calls = self.run_switch({'start': 'isuride-rust.service', 'stop': ['isuride-go.service'],
                                         'nodes': ['app1', 'app2']})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call.split('\t')[0] for call in calls], ['app1', 'app2'])
        self.assertIn('sudo systemctl disable --now isuride-go.service', calls[0])
        self.assertIn('sudo systemctl enable --now isuride-rust.service', calls[0])
        self.assertIn('is-active --quiet isuride-rust.service', calls[0])

    def test_without_a_plan_nothing_runs(self):
        result, calls = self.run_switch(None)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_rejects_unit_names_that_could_inject_commands(self):
        result, calls = self.run_switch({'start': 'x.service; reboot', 'stop': [], 'nodes': ['app1']})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
