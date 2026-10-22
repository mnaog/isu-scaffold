import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
import os
import re

ROOT = Path(__file__).resolve().parents[1]


class KickoffSetupTest(unittest.TestCase):
    def test_the_policy_overlay_names_commands_that_exist(self):
        import tomllib
        policy = tomllib.loads((ROOT / '.isuscope/policy.toml').read_text())
        history = Path(policy['context']['agent']['history_dir'])
        self.assertFalse(history.is_absolute() or '..' in history.parts)  # isuscope refuses both
        benchmark = policy['benchmark']
        for key in ('prepare_command', 'rescore_command', 'identity_command', 'cleanup_command'):
            with self.subTest(key):
                self.assertTrue((ROOT / benchmark[key][0]).is_file(), benchmark[key])
        for parser in benchmark['parsers']:
            self.assertTrue((ROOT / parser['command'][0]).is_file(), parser)
        # The benchmark's deadline is the adapter's alone (config/benchmark.env).
        self.assertNotIn('timeout_seconds', benchmark)

    def test_discover_includes_the_policy_in_the_generated_config(self):
        render = (ROOT / 'scripts/render-environment.sh').read_text()
        self.assertIn('config = [".isuscope/policy.toml"]', render)

    def test_review_checks_the_slow_log_only_when_bootstrap_turns_it_on(self):
        spec = importlib.util.spec_from_file_location('review', ROOT / 'scripts/review-draft.py')
        review = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(review)
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            self.assertFalse(review.slow_log_enabled(repo, {}))
            (repo / 'ansible/playbooks/group_vars').mkdir(parents=True)
            (repo / 'ansible/playbooks/group_vars/all.yml').write_text('observability_mysql_slow_log: false\n')
            self.assertFalse(review.slow_log_enabled(repo, {}))
            self.assertTrue(review.slow_log_enabled(repo, {'observability_mysql_slow_log': True}))
        self.assertIn('observability_mysql_slow_log: false',
                      (ROOT / 'ansible/playbooks/group_vars/all.yml').read_text())

    def test_review_lists_node_types_and_warns_when_they_or_their_speeds_differ(self):
        # A stack may mix instance types (c7i ×3 and c6in ×2 in one environment check).
        spec = importlib.util.spec_from_file_location('review', ROOT / 'scripts/review-draft.py')
        review = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(review)

        def report(kind, cpu, single, every):
            return {'instance_type': kind, 'cpu_model': cpu, 'processor_vcpus': 2, 'memory_mb': 3800,
                    'cpu_speed': {'loop_per_second': single, 'loop_all_vcpus_per_second': every,
                                  'sha256_mb_per_second': 800}}
        nodes = ['app1', 'app2', 'app3', 'app4', 'app5']
        inspections = {f'app{n}': report('c7i.large', 'Xeon 8488C', 10000, 12500) for n in (1, 2, 3)}
        inspections.update({f'app{n}': report('c6in.large', 'Xeon 8375C', 9000, 11000) for n in (4, 5)})
        lines, warnings = review.node_table(nodes, inspections, {})
        table = '\n'.join(lines)
        self.assertIn('| app1 | c7i.large | Xeon 8488C | 2 | 3800 | 100% | 100% | 800 |', table)
        self.assertIn('| app4 | c6in.large | Xeon 8375C | 2 | 3800 | 90% | 88% | 800 |', table)
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn('c6in.large ×2 (app4, app5) / c7i.large ×3 (app1, app2, app3)', warnings[0])

        # One type, but one node lands on another CPU and one runs far slower.
        same = {f'app{n}': report('c5.large', 'Xeon 8124M', 10000, 12000) for n in (1, 2)}
        same['app3'] = report('c5.large', 'Xeon 8275CL', 8000, 9500)
        lines, warnings = review.node_table(['app1', 'app2', 'app3'], same, {})
        self.assertTrue(any('run different CPUs' in warning for warning in warnings), warnings)
        self.assertTrue(any(warning.startswith('app3 measured more than 15% slower') for warning in warnings), warnings)

        # Without inspection reports the type comes from discovery and speeds stay blank.
        lines, warnings = review.node_table(['app1'], {}, {'app1': {'instance_type': 'c7i.large'}})
        self.assertIn('| app1 | c7i.large | — | — | — | — | — | — |', lines)
        self.assertEqual(warnings, [])

    def test_review_sizes_a_directory_without_its_excluded_build_output(self):
        # practice-12-sixth: rust-app warned at 1048 MiB although import and deploy skip target/.
        spec = importlib.util.spec_from_file_location('review', ROOT / 'scripts/review-draft.py')
        review = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(review)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bin').mkdir()
            (root / 'bin/sudo').write_text('#!/bin/sh\n[ "$1" = -n ] && shift\nexec "$@"\n')
            (root / 'bin/sudo').chmod(0o755)
            (root / 'app/target').mkdir(parents=True)
            (root / 'app/target/cache').write_bytes(b'\0' * 4 * 1024 * 1024)
            (root / 'app/main.rs').write_text('fn main() {}\n')
            app = str(root / 'app')
            checks = {'paths': {app}, 'excludes': {app: ('target',)}, 'users': set(), 'groups': set(),
                      'services': set(), 'dropins': set(), 'logs': set()}
            output = subprocess.run(['bash'], input=review.remote_script(checks), capture_output=True, text=True,
                                    env={**os.environ, 'PATH': str(root / 'bin') + ':' + os.environ['PATH']},
                                    check=True).stdout
            kind, size = output.strip().split('\t')[2:]
            self.assertEqual(kind, 'directory')
            self.assertLess(int(size), 1024)

    def run_guard(self, cloud_exit):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'cloud-init').write_text(f'#!/bin/sh\nexit {cloud_exit}\n')
            (path / 'ps').write_text('#!/bin/sh\nexit 0\n')
            for file in path.iterdir():
                file.chmod(0o755)
            return subprocess.run(['bash', str(ROOT / 'ansible/playbooks/wait-provision.sh')],
                                  env={**os.environ, 'PATH': directory + ':' + os.environ['PATH']},
                                  capture_output=True).returncode

    def test_failed_cloud_init_blocks_bootstrap(self):
        self.assertEqual(self.run_guard(1), 1)
        self.assertEqual(self.run_guard(0), 0)

    def test_inspection_never_visits_a_benchmark_node(self):
        # In the contest the benchmark node may sit in the participant's account and belong to the organizer.
        text = (ROOT / 'ansible/playbooks/inspect.yml').read_text()
        hosts = re.findall(r'^  hosts:\s*(\S+)', text, re.M)
        self.assertEqual(hosts, ['application'])

    def test_inspection_includes_stopped_units(self):
        import textwrap
        source = (ROOT / 'ansible/playbooks/inspect.yml').read_text()
        task = source.split('    - name: Find systemd service fragment paths\n', 1)[1]
        command = textwrap.dedent(task.split('ansible.builtin.shell: |\n', 1)[1].split('      args:', 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'systemctl'
            binary.write_text("""#!/bin/sh
case "$1" in
list-unit-files) echo 'isu-rust.service disabled enabled';;
list-units) echo 'nginx.service loaded active running';;
show) shift; test "$1" = --property=FragmentPath && shift 2
  for unit; do printf 'FragmentPath=/etc/systemd/system/%s\n\n' "$unit"; done;;
esac
""")
            binary.chmod(0o755)
            result = subprocess.check_output(['bash', '-c', command], text=True,
                env={**os.environ, 'PATH': directory + ':' + os.environ['PATH']})
            self.assertIn('isu-rust.service\t/etc/systemd/system/isu-rust.service', result)
            self.assertIn('nginx.service\t/etc/systemd/system/nginx.service', result)

    def test_vendor_rust_unit_can_be_a_draft_candidate(self):
        spec = importlib.util.spec_from_file_location('draft', ROOT / 'scripts/configure-draft.py')
        draft = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(draft)
        self.assertEqual(draft.rust_service(['isu-rust.service\t/lib/systemd/system/isu-rust.service']),
                         ('isu-rust', '/lib/systemd/system/isu-rust.service'))
