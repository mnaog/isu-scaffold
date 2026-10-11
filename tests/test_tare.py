import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent

NGINX_CONF = """user www-data;
worker_processes auto;
pid /run/nginx.pid;
error_log /var/log/nginx/error.log;

events {
\tworker_connections 768;
}

http {
\tsendfile on;
\taccess_log /var/log/nginx/access.log;
\tinclude /etc/nginx/conf.d/*.conf;
\tinclude /etc/nginx/sites-enabled/*;
}
"""

SITE = """server {
  listen 80 default_server;
  location / { try_files $uri $uri/ =404; }
}

server {
  listen 443 ssl default_server;
  server_name _;
}

server {
  listen 443 ssl;
  server_name app.example.test;
  proxy_set_header X-Forwarded-For $remote_addr;

  location /api/ {
    proxy_set_header Host $host;
    proxy_pass http://localhost:8080;
  }

  location /internal/ {
    # only from the node itself
    allow 127.0.0.1;
    deny all;
    proxy_pass http://localhost:8080/internal/;
  }

  location /dynamic/ {
    proxy_pass http://$backend;
  }
}
"""

UNIT = """[Unit]
Description=app

[Service]
User=isucon
ExecStart=/home/isucon/webapp/rust/target/release/app

[Install]
WantedBy=multi-user.target
"""


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True).stdout


class TareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        files = {
            'config/nginx/nginx.conf': NGINX_CONF,
            'config/nginx/sites-available/app.conf': SITE,
            'config/systemd/app-rust.service': UNIT,
            'config/mysql/mysql.conf.d/mysqld.cnf': '[mysqld]\nbind-address = 127.0.0.1\n',
        }
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.sync = {
            'source_node': 'app1',
            'items': [
                {'name': 'rust-service', 'type': 'file', 'node_group': 'role_app', 'local': 'config/systemd/app-rust.service',
                 'remote': '/etc/systemd/system/app-rust.service', 'owner': 'root', 'owner_group': 'root'},
                {'name': 'nginx-conf', 'type': 'file', 'node_group': 'role_nginx', 'local': 'config/nginx/nginx.conf',
                 'remote': '/etc/nginx/nginx.conf', 'owner': 'root', 'owner_group': 'root'},
                {'name': 'nginx-site-app', 'type': 'file', 'node_group': 'role_nginx',
                 'local': 'config/nginx/sites-available/app.conf', 'remote': '/etc/nginx/sites-available/app.conf',
                 'owner': 'root', 'owner_group': 'root'},
                {'name': 'mysql-mysql.conf.d', 'type': 'directory', 'node_group': 'role_mysql', 'source_node': 'app1',
                 'local': 'config/mysql/mysql.conf.d', 'remote': '/etc/mysql/mysql.conf.d', 'owner': 'root', 'owner_group': 'root'},
            ],
            'post_deploy_commands': [
                {'node_group': 'role_nginx', 'command': 'sudo nginx -t'},
                {'node_group': 'role_app', 'command': 'sudo systemctl daemon-reload'},
                {'node_group': 'role_app', 'command': 'sudo systemctl restart app-rust'},
                'sudo systemctl is-active --quiet mysql',
            ],
            'rollback_commands': [],
        }
        (self.root / 'config/sync.json').write_text(json.dumps(self.sync, indent=2) + '\n')
        (self.root / '.local/inspection').mkdir(parents=True)
        (self.root / '.local/inspection/app1.json').write_text(json.dumps(
            {'memory_mb': 3800, 'running_services': ['nginx.service', 'mysql.service', 'app-rust.service']}))
        git(self.root, 'init', '-q')
        git(self.root, 'config', 'user.name', 'Fixture')
        git(self.root, 'config', 'user.email', 'fixture@example.invalid')
        (self.root / '.gitignore').write_text('.local/\n')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-qm', 'initial state')

    def tearDown(self):
        self.tmp.cleanup()

    def run_tare(self, *args):
        return subprocess.run(['python3', str(ROOT / 'scripts/tare.py'), '--root', str(self.root), *args],
                              capture_output=True, text=True)

    def read(self, name):
        return (self.root / name).read_text()

    def test_one_commit_with_every_limit(self):
        result = self.run_tare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Raise connection and file limits (make tare)', git(self.root, 'log', '-1', '--format=%s'))
        self.assertEqual(git(self.root, 'status', '--porcelain'), '')

        nginx = self.read('config/nginx/nginx.conf')
        self.assertIn('worker_processes auto;\nworker_rlimit_nofile 131072;', nginx)
        self.assertIn('worker_connections 32768;', nginx)
        self.assertNotIn('worker_connections 768', nginx)
        self.assertIn('keepalive_requests 10000;', nginx)
        self.assertIn('error_log /var/log/nginx/error.log warn;', nginx)

        site = self.read('config/nginx/sites-available/app.conf')
        self.assertIn('listen 80 default_server backlog=4096;', site)
        self.assertIn('listen 443 ssl default_server backlog=4096;', site)
        self.assertIn('listen 443 ssl;', site)  # one backlog per address:port
        self.assertEqual(site.count('upstream tare_localhost_8080 {'), 1)
        self.assertIn('keepalive 128;', site)
        # The location with its own headers keeps them and gains HTTP/1.1 and an empty Connection.
        api = site[site.index('location /api/'):site.index('location /internal/')]
        self.assertIn('proxy_set_header Host $host;', api)
        self.assertIn('proxy_http_version 1.1;', api)
        self.assertIn('proxy_set_header Connection "";', api)
        self.assertIn('proxy_pass http://tare_localhost_8080;', api)
        # The location that inherited the server's headers copies them and keeps the old Host.
        internal = site[site.index('location /internal/'):site.index('location /dynamic/')]
        self.assertIn('proxy_set_header X-Forwarded-For $remote_addr;', internal)
        self.assertIn('proxy_set_header Host localhost:8080;', internal)
        self.assertIn('proxy_pass http://tare_localhost_8080/internal/;', internal)
        self.assertIn('proxy_pass http://$backend;', site)

        self.assertIn('LimitNOFILE=1048576', self.read('config/systemd/app-rust.service').split('[Install]')[0])
        self.assertIn('net.core.somaxconn = 65535', self.read('config/sysctl/99-tare.conf'))
        mysql = self.read('config/mysql/mysql.conf.d/zz-tare.cnf')
        self.assertIn('max_connections = 1000', mysql)
        self.assertIn('innodb_buffer_pool_size = 1408M', mysql)  # 40% of 3800 MiB beside nginx and the app

        sync = json.loads(self.read('config/sync.json'))
        self.assertTrue(any(item['remote'] == '/etc/sysctl.d/99-tare.conf' for item in sync['items']))
        commands = [c if isinstance(c, str) else c['command'] for c in sync['post_deploy_commands']]
        self.assertEqual(commands[:2], ['sudo systemctl daemon-reload', 'sudo sysctl --system >/dev/null'])
        self.assertLess(commands.index('sudo systemctl restart mysql'), commands.index('sudo systemctl is-active --quiet mysql'))
        self.assertIn('sudo systemctl daemon-reload', [c if isinstance(c, str) else c['command'] for c in sync['rollback_commands']])

    def test_rerun_changes_nothing(self):
        self.assertEqual(self.run_tare().returncode, 0)
        head = git(self.root, 'rev-parse', 'HEAD')
        result = self.run_tare()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('already applied', result.stdout)
        self.assertEqual(git(self.root, 'rev-parse', 'HEAD'), head)

    def test_refuses_to_mix_with_uncommitted_edits(self):
        path = self.root / 'config/nginx/nginx.conf'
        path.write_text(path.read_text() + '# local edit\n')
        result = self.run_tare()
        self.assertEqual(result.returncode, 1)
        self.assertIn('uncommitted', result.stderr)

    def test_existing_backlog_and_upstreams_are_kept(self):
        site = self.root / 'config/nginx/sites-available/app.conf'
        site.write_text(SITE.replace('listen 443 ssl;', 'listen 443 ssl backlog=1024;'))
        git(self.root, 'commit', '-qam', 'own backlog')
        self.assertEqual(self.run_tare().returncode, 0)
        text = site.read_text()
        self.assertNotIn('listen 443 ssl default_server backlog=4096;', text)
        self.assertIn('listen 443 ssl backlog=1024;', text)


if __name__ == '__main__':
    unittest.main()
