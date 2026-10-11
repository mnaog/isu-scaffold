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
  is-active) exit 0 ;;
  show) case "$3" in ControlGroup) echo /system.slice/isu-rust.service ;; MainPID) echo 4321 ;; esac ;;
esac
''', 0o755)
        write(stubs / 'ss', '#!/bin/sh\ncat "$SS_OUTPUT"\n', 0o755)
        self.listening = self.root / 'ss.out'
        write(self.root / 'ssh-node.sh', f'#!/bin/sh\nNODE="$1" PATH={stubs}:$PATH exec bash -c "$2"\n', 0o755)
        (self.root / 'prepare.sh').chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def run_prepare(self, wait='2'):
        # The script calls ssh-node.sh next to itself.
        return subprocess.run([str(self.root / 'prepare.sh')], capture_output=True, text=True, env={
            **os.environ, 'SYNC_MANIFEST': str(self.root / 'config/sync.json'),
            'ANSIBLE_INVENTORY': str(self.root / 'inventory.json'), 'SS_OUTPUT': str(self.listening),
            'PREPARE_WAIT_SECONDS': wait})

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

    def test_without_an_application_unit_there_is_nothing_to_restart(self):
        write(self.root / 'config/sync.json', json.dumps({'items': []}))
        result = self.run_prepare()
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())


if __name__ == '__main__':
    unittest.main()
