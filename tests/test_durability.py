import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout


class DurabilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        path = self.root / 'config/mysql/mysql.conf.d/mysqld.cnf'
        path.parent.mkdir(parents=True)
        path.write_text('[mysqld]\nbind-address = 127.0.0.1\n')
        sync = {
            'source_node': 'app1',
            'items': [{'name': 'mysql-mysql.conf.d', 'type': 'directory', 'node_group': 'role_mysql', 'source_node': 'app2',
                       'local': 'config/mysql/mysql.conf.d', 'remote': '/etc/mysql/mysql.conf.d',
                       'owner': 'root', 'owner_group': 'root'}],
            'post_deploy_commands': ['sudo systemctl is-active --quiet mysql'],
            'rollback_commands': [],
        }
        (self.root / 'config/sync.json').write_text(json.dumps(sync, indent=2) + '\n')
        (self.root / '.local/inspection').mkdir(parents=True)
        git(self.root, 'init', '-q')
        git(self.root, 'config', 'user.name', 'Fixture')
        git(self.root, 'config', 'user.email', 'fixture@example.invalid')
        (self.root / '.gitignore').write_text('.local/\n')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'initial state')

    def tearDown(self):
        self.tmp.cleanup()

    def inspected(self, version):
        (self.root / '.local/inspection/app2.json').write_text(json.dumps({'versions': {'mysql': version}}))

    def run_durability(self):
        return subprocess.run(['python3', str(ROOT / 'scripts/durability.py'), '--root', str(self.root)],
                              capture_output=True, text=True)

    def cnf(self):
        return (self.root / 'config/mysql/mysql.conf.d/zz-durability.cnf').read_text()

    def test_one_commit_for_mysql_8_0_30_and_later(self):
        self.inspected('mysql  Ver 8.0.46-0ubuntu0.24.04.3 for Linux on x86_64 ((Ubuntu))')
        result = self.run_durability()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('(make durability)', git(self.root, 'log', '-1', '--format=%s'))
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')
        cnf = self.cnf()
        for line in ('innodb_flush_log_at_trx_commit = 2', 'skip-log-bin', 'innodb_doublewrite = OFF',
                     'innodb_log_buffer_size = 64M', 'innodb_redo_log_capacity = 1G'):
            self.assertIn(line + '\n', cnf)
        commands = [c if isinstance(c, str) else c['command'] for c in
                    json.loads((self.root / 'config/sync.json').read_text())['post_deploy_commands']]
        self.assertEqual(commands, ['sudo systemctl restart mysql', 'sudo systemctl is-active --quiet mysql'])
        # Rerunning changes nothing.
        head = git(self.root, 'rev-parse', 'HEAD')
        self.assertIn('already applied', self.run_durability().stdout)
        self.assertEqual(git(self.root, 'rev-parse', 'HEAD'), head)

    def test_redo_option_follows_the_server(self):
        self.inspected('mysql  Ver 8.0.29-0ubuntu0.20.04.3 for Linux on x86_64 ((Ubuntu))')
        self.assertEqual(self.run_durability().returncode, 0)
        self.assertIn('innodb_log_file_size = 512M', self.cnf())
        self.assertNotIn('redo_log_capacity', self.cnf())

    def test_mariadb(self):
        self.inspected('mysql  Ver 15.1 Distrib 10.6.18-MariaDB, for debian-linux-gnu (x86_64)')
        self.assertEqual(self.run_durability().returncode, 0)
        self.assertIn('innodb_log_file_size = 1G', self.cnf())

    def test_unknown_version_leaves_the_redo_size(self):
        result = self.run_durability()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('log_file_size', self.cnf())
        self.assertNotIn('redo_log_capacity', self.cnf())
        self.assertIn('version is unknown', result.stdout)


if __name__ == '__main__':
    unittest.main()
