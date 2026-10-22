import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BootstrapSshTests(unittest.TestCase):
    """kickoff reaches the source node first and the others during the code transfer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / 'repo'
        (self.repo / 'scripts').mkdir(parents=True)
        for name in ('bootstrap-ssh.sh', 'parallel-lib.sh', 'ssh-node.sh'):
            shutil.copy(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        local = self.repo / '.local'
        local.mkdir()
        (local / 'id').write_text('key')
        (local / 'environment.env').write_text('SSH_BOOTSTRAP_METHOD=existing\nSSH_IDENTITY_FILE=.local/id\n')
        hosts = {name: {'ansible_host': f'{name}.example', 'ansible_user': 'isucon'} for name in ('app1', 'app2')}
        (local / 'ansible-inventory.json').write_text(json.dumps({'all': {'children': {
            'application': {'hosts': hosts}, 'benchmark': {'hosts': {'bench': {'ansible_host': 'bench.example', 'ansible_user': 'isucon'}}}}}}))
        bin_dir = Path(self.tmp.name) / 'bin'
        bin_dir.mkdir()
        self.calls = Path(self.tmp.name) / 'ssh-calls'
        (bin_dir / 'ssh').write_text(f'#!/bin/sh\nfor a; do last=$prev; prev=$a; done\necho "$last" >> {self.calls}\n')
        (bin_dir / 'ssh').chmod(0o755)
        self.env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}', ISUSCOPE_LOCK_HELD='1')

    def tearDown(self):
        self.tmp.cleanup()

    def run_ssh(self, **env):
        return subprocess.run(['bash', str(self.repo / 'scripts/bootstrap-ssh.sh')], cwd=self.repo,
                              env=dict(self.env, **env), capture_output=True, text=True)

    def reached(self):
        return sorted(self.calls.read_text().split()) if self.calls.exists() else []

    def set_access(self, access):
        path = self.repo / '.local/ansible-inventory.json'
        inventory = json.loads(path.read_text())
        inventory['all']['vars'] = {'isucon_benchmark_node_access': access, 'ansible_ssh_private_key_file': '.local/id',
                                    'ansible_ssh_common_args': '-o BatchMode=yes'}
        path.write_text(json.dumps(inventory))

    def test_except_node_reaches_every_other_node(self):
        self.set_access('ssh')
        result = self.run_ssh(SSH_EXCEPT_NODE='app1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.reached(), ['isucon@app2.example', 'isucon@bench.example'])

    def test_the_benchmark_node_is_left_alone_by_default(self):
        # In the contest a benchmark node in the participant's account is the organizer's.
        for access in (None, 'none'):
            with self.subTest(access=access):
                if access:
                    self.set_access(access)
                self.calls.unlink(missing_ok=True)
                result = self.run_ssh()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.reached(), ['isucon@app1.example', 'isucon@app2.example'])

    def test_ssh_node_refuses_the_benchmark_node_even_for_the_adapter_unless_access_is_ssh(self):
        def connect(node, **env):
            return subprocess.run(['bash', str(self.repo / 'scripts/ssh-node.sh'), node, 'uptime'], cwd=self.repo,
                                  env=dict(self.env, **env), capture_output=True, text=True)
        self.set_access('none')
        refused = connect('bench', ISUCON_ALLOW_BENCHMARK_NODE='1')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('BENCHMARK_NODE_ACCESS is none', refused.stderr)
        self.assertEqual(self.reached(), [])
        self.assertEqual(connect('app1').returncode, 0)
        self.set_access('ssh')
        self.assertIn('run benchmarks through isuscope', connect('bench').stderr)
        self.assertEqual(connect('bench', ISUCON_ALLOW_BENCHMARK_NODE='1').returncode, 0)
        self.assertEqual(self.reached(), ['isucon@app1.example', 'isucon@bench.example'])

    def test_except_the_only_node_is_not_an_error(self):
        result = self.run_ssh(SSH_ONLY_NODE='app1', SSH_EXCEPT_NODE='app1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.reached(), [])

    def test_only_node_without_a_match_still_fails(self):
        result = self.run_ssh(SSH_ONLY_NODE='missing')
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
