import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


def write(path, text, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode:
        path.chmod(mode)


class BenchmarkPrepareTest(unittest.TestCase):
    """The prepare step restarts the running application and waits until it listens."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copy(ROOT / 'scripts/benchmark-prepare.sh', self.root / 'prepare.sh')
        write(self.root / 'config/sync.json', json.dumps({'items': [
            {'name': 'rust-service', 'type': 'file', 'node_group': 'role_app', 'local': 'config/systemd/app.service',
             'remote': '/etc/systemd/system/isu-rust.service', 'owner': 'root', 'owner_group': 'root'}]}))
        write(self.root / 'inventory.json', json.dumps({'all': {'children': {
            'role_app': {'hosts': {'app1': {}, 'app2': {}}},
            'benchmark': {'hosts': {'bench': {}}}}}}))
        self.calls = self.root / 'calls.log'
        # ssh-node.sh runs the remote script locally, with stubbed systemctl, sudo and ss.
        stubs = self.root / 'stubs'
        write(stubs / 'sudo', '#!/bin/sh\nexec "$@"\n', 0o755)
        write(stubs / 'systemctl', f'''#!/bin/sh
case "$1" in
  try-restart) echo "$NODE restart $2" >> {self.calls} ;;
  is-active) [ -z "$INACTIVE" ] ;;
  show) case "$3" in ControlGroup) echo /system.slice/isu-rust.service ;; MainPID) echo 4321 ;; esac ;;
esac
''', 0o755)
        write(stubs / 'ss', '#!/bin/sh\ncat "$SS_OUTPUT"\n', 0o755)
        self.listening = self.root / 'ss.out'
        write(self.root / 'ssh-node.sh', f'#!/bin/sh\nNODE="$1" PATH={stubs}:$PATH exec bash -c "$2"\n', 0o755)
        (self.root / 'prepare.sh').chmod(0o755)
        # The commit check has its own tests below.
        write(self.root / 'check-deployed-commit.sh', '#!/bin/sh\necho deployed commit matches\n', 0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def run_prepare(self, wait='2', **env):
        # The script calls ssh-node.sh next to itself.
        base = {k: v for k, v in os.environ.items() if k not in ('INACTIVE', 'REQUIRE_ACTIVE')}
        return subprocess.run([str(self.root / 'prepare.sh')], capture_output=True, text=True, env={
            **base, 'SYNC_MANIFEST': str(self.root / 'config/sync.json'),
            'ANSIBLE_INVENTORY': str(self.root / 'inventory.json'), 'SS_OUTPUT': str(self.listening),
            'PREPARE_WAIT_SECONDS': wait, **env})

    def test_restarts_on_application_nodes_and_waits_for_listening(self):
        write(self.listening, 'LISTEN 0 4096 0.0.0.0:8080 0.0.0.0:* users:(("isu-app",pid=4321,fd=9))\n')
        result = self.run_prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = sorted(self.calls.read_text().splitlines())
        self.assertEqual(calls, ['app1 restart isu-rust.service', 'app2 restart isu-rust.service'])
        self.assertIn('restarted and listening', result.stdout)

    def test_fails_when_the_application_never_listens(self):
        write(self.listening, '')
        result = self.run_prepare(wait='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('did not start listening', result.stderr)

    def test_a_stopped_unit_passes_unless_it_must_be_running(self):
        # make first-bench benchmarks the distributed build only when it really runs.
        write(self.listening, '')
        self.assertEqual(self.run_prepare(wait='1', INACTIVE='1').returncode, 0)
        result = self.run_prepare(wait='1', INACTIVE='1', REQUIRE_ACTIVE='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('did not start listening', result.stderr)

    def test_without_an_application_unit_there_is_nothing_to_restart(self):
        write(self.root / 'config/sync.json', json.dumps({'items': []}))
        result = self.run_prepare()
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())


class DeployedCommitCheckTest(unittest.TestCase):
    """The benchmark does not start while the servers may run another commit than HEAD."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.git('init', '-q')
        self.git('config', 'user.email', 't@example.com')
        self.git('config', 'user.name', 't')
        write(self.repo / 'config/sync.json', json.dumps({
            'items': [{'name': 'rust-app', 'local': 'webapp/rust'}],
            'local_builds': [{'item': 'rust-app', 'dockerfile': 'config/rust-builder/Dockerfile'}]}))
        write(self.repo / 'webapp/rust/src/main.rs', 'fn main() {}\n')
        write(self.repo / 'config/rust-builder/Dockerfile', 'FROM rust\n')
        write(self.repo / 'docs/notes.md', 'notes\n')
        self.commit('initial')
        write(self.repo / '.local/current-deploy-commit', self.git('rev-parse', 'HEAD') + '\n')

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self, message):
        self.git('add', '-A', '--', '.', ':!.local')
        self.git('commit', '-q', '-m', message)

    def check(self, **env):
        return subprocess.run([str(ROOT / 'scripts/check-deployed-commit.sh')], capture_output=True, text=True,
                              env={**os.environ, 'REPO_DIR': str(self.repo), **env})

    def test_commits_outside_the_deployed_files_do_not_stop_the_benchmark(self):
        write(self.repo / 'docs/notes.md', 'run recorded\n')
        self.commit('Record a run')
        write(self.repo / 'isuscope-data/new.json', '{}')
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('matches HEAD', result.stdout)

    def test_a_commit_that_was_not_deployed_stops_the_benchmark(self):
        # practice-14 eb28e94a: the deploy was refused, the run was recorded under the new commit.
        write(self.repo / 'webapp/rust/src/main.rs', 'fn main() { println!(); }\n')
        self.commit('Change the app')
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn('webapp/rust/src/main.rs', result.stderr)

    def test_uncommitted_changes_in_deployed_files_stop_the_benchmark(self):
        write(self.repo / 'config/rust-builder/Dockerfile', 'FROM rust:1\n')
        write(self.repo / 'webapp/rust/src/new.rs', '\n')
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn('config/rust-builder/Dockerfile', result.stderr)
        self.assertIn('webapp/rust/src/new.rs', result.stderr)

    def test_no_recorded_deploy_stops_the_benchmark_unless_skipped(self):
        (self.repo / '.local/current-deploy-commit').unlink()
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn('make deploy', result.stderr)
        result = self.check(SKIP_DEPLOYED_COMMIT_CHECK='1')
        self.assertEqual(result.returncode, 0)
        self.assertIn('WARN', result.stderr)

    def test_before_the_first_deploy_the_imported_commit_is_what_runs(self):
        imported = self.git('rev-parse', 'HEAD')
        (self.repo / '.local/current-deploy-commit').unlink()
        write(self.repo / '.local/current-import-commit', imported + '\n')
        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('imported', result.stdout)
        write(self.repo / 'webapp/rust/src/main.rs', 'fn main() { println!(); }\n')
        self.commit('Change the app')
        result = self.check()
        self.assertEqual(result.returncode, 1)
        self.assertIn('differs from the imported', result.stderr)
        # Once deployed, the deploy record wins over the import.
        write(self.repo / '.local/current-deploy-commit', self.git('rev-parse', 'HEAD') + '\n')
        self.assertEqual(self.check().returncode, 0)


if __name__ == '__main__':
    unittest.main()
