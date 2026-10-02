#!/usr/bin/env python3
"""Prepare medium trials using the existing pinned native toolchain and isolated MySQL."""
import argparse
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import manage as m

SPEC = json.loads((m.ROOT / 'config/ai-compare/medium.json').read_text())
DB = m.ROOT / '.local/ai-compare/mysql'
BIN = DB / SPEC['mysql_archive'] / 'bin'
SOCKET = '/tmp/isu-ai-compare-mysql.sock'
URL = f"mysql://root@127.0.0.1:{SPEC['mysql_port']}/new_user_cache_test"
INCLUDE = '\ninclude!("medium_acceptance.rs");\n'


def db_setup(_args):
    DB.mkdir(parents=True, exist_ok=True)
    archive = DB / 'mysql.tar.gz'
    if not archive.exists():
        urllib.request.urlretrieve(SPEC['mysql_url'], archive)
    if m.digest(archive) != SPEC['mysql_sha256']:
        raise SystemExit('MySQL archive hash mismatch')
    if not BIN.exists():
        with tarfile.open(archive) as tar:
            tar.extractall(DB, filter='data')
    config = DB / 'my.cnf'
    config.write_text(f'''[mysqld]
basedir={BIN.parent}
datadir={DB / 'data'}
port={SPEC['mysql_port']}
bind-address=127.0.0.1
socket={SOCKET}
pid-file={DB / 'mysqld.pid'}
log-error={DB / 'mysqld.log'}
mysqlx=0
skip-log-bin
innodb-buffer-pool-size=128M
''')
    if not (DB / 'data').exists():
        m.execute([BIN / 'mysqld', '--defaults-file=' + str(config), '--initialize-insecure'])
    if not (DB / 'mysqld.pid').exists():
        m.execute([BIN / 'mysqld', '--defaults-file=' + str(config), '--daemonize'])
    actual = m.output([BIN / 'mysql', '--no-defaults', '--socket=' + SOCKET, '-uroot', '-NBe',
                       'SELECT @@datadir, @@port'])
    if actual.split() != [str(DB / 'data') + '/', str(SPEC['mysql_port'])]:
        raise SystemExit('Unexpected MySQL instance; refusing to initialize DB')
    m.execute([BIN / 'mysql', '--no-defaults', '--socket=' + SOCKET, '-uroot', '-e',
               'CREATE DATABASE IF NOT EXISTS new_user_cache_test'])
    m.write_json(DB / 'environment.json', {'spec': SPEC,
        'version': m.output([BIN / 'mysqld', '--no-defaults', '--version']),
        'mysqld_sha256': m.digest(BIN / 'mysqld'), 'config_sha256': m.digest(config)})
    print('Dedicated MySQL ready: ' + URL)


def prepare(args):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', args.trial):
        raise SystemExit('Invalid trial ID')
    if (m.STATE / 'trials' / (args.trial + '.json')).exists():
        raise SystemExit('Trial ID already registered')
    saved = json.loads((m.STATE / 'environment.json').read_text())
    if m.fingerprint(m.build_environment(saved['build_env'])) != saved['fingerprint']:
        raise SystemExit('Host/toolchain changed')
    db = json.loads((DB / 'environment.json').read_text())
    if m.digest(BIN / 'mysqld') != db['mysqld_sha256'] or m.digest(DB / 'my.cnf') != db['config_sha256']:
        raise SystemExit('MySQL changed')
    dest = args.destination.resolve() if args.destination else m.ROOT.parent / 'ai-medium-trials' / args.trial
    if dest.exists():
        raise SystemExit('Destination exists; use a new trial')
    archive = subprocess.check_output(['git', '-C', str(args.source), 'archive', SPEC['source_commit'], *SPEC['source_paths']])
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter='data')
    if m.digest(dest / 'webapp/rust/Cargo.lock') != m.digest(m.STATE / 'seed/webapp/rust/Cargo.lock'):
        raise SystemExit('Dependency lock differs from prepared vendor cache')
    (dest / '.local').mkdir(mode=0o700)
    (dest / '.cargo').mkdir()
    (dest / '.claude/rules').mkdir(parents=True)
    (dest / '.claude/rules/agents.md').symlink_to('../../AGENTS.md')
    for src, target in [('config/ai-compare/medium-AGENTS.md', 'AGENTS.md'),
                        ('config/ai-compare/medium-task.txt', 'TASK.txt'),
                        ('scripts/ai-compare/verify.py', 'verify.py'),
                        ('config/ai-compare/medium_acceptance.rs', 'webapp/rust/src/medium_acceptance.rs')]:
        shutil.copyfile(m.ROOT / src, dest / target)
    p = dest / 'webapp/rust/src/new_user_cache_integration.rs'
    p.write_text(p.read_text() + INCLUDE)
    shutil.copyfile(m.STATE / 'cargo-config.toml', dest / '.cargo/config.toml')
    toolchain = m.SPEC['toolchain'] + '-' + m.SPEC['host']
    (dest / 'rust-toolchain.toml').write_text(f'[toolchain]\nchannel = "{toolchain}"\nprofile = "minimal"\n')
    (dest / '.gitignore').write_text('/.local/\n/webapp/rust/target/\n__pycache__/\n')
    m.execute(['/bin/cp', '-cR', m.STATE / 'seed-target', dest / '.local/target'])
    config = {'trial': args.trial, 'toolchain': toolchain, 'cargo': str(m.STATE / 'cargo/bin/cargo'),
              'build_env': saved['build_env'], 'build_lock': str(m.STATE / 'build.lock'), 'database_url': URL}
    m.write_json(dest / '.local/environment.json', config)
    m.execute(['git', 'init', '-q', '-b', 'main', dest])
    with (dest / '.git/info/exclude').open('a') as f:
        f.write('\n/docs/agent-history/\n')
    for k, v in [('user.name', 'AI comparison'), ('user.email', 'ai-compare@localhost'), ('commit.gpgsign', 'false')]:
        m.execute(['git', '-C', dest, 'config', k, v])
    m.execute(['git', '-C', dest, 'add', '.'])
    env = dict(os.environ, GIT_AUTHOR_DATE='2026-10-01T00:00:00+00:00', GIT_COMMITTER_DATE='2026-10-01T00:00:00+00:00')
    m.execute(['git', '-C', dest, '-c', 'core.hooksPath=/dev/null', 'commit', '-q', '-m', 'Initial medium task snapshot'], env=env)
    for mode in ('check', 'test', 'mysql', 'acceptance'):
        path = dest / f'.local/preparation-{mode}.log'
        with path.open('w') as log:
            code = subprocess.run([sys.executable, str(dest / 'verify.py'), mode], stdout=log, stderr=subprocess.STDOUT).returncode
        if code != (101 if mode == 'acceptance' else 0):
            raise SystemExit(f'Unexpected {mode} status {code}: {path}')
        if mode == 'acceptance' and 'inventory must remain complete: after_registration' not in path.read_text():
            raise SystemExit('Acceptance failed for unexpected reason: ' + str(path))
    (dest / '.local/validation.jsonl').rename(dest / '.local/preparation.jsonl')
    files = m.output(['git', '-C', dest, 'ls-files']).splitlines()
    import hashlib
    manifest = {'trial': args.trial, 'destination': str(dest), 'environment': saved,
                'environment_file_sha256': m.digest(dest / '.local/environment.json'),
                'source_archive_sha256': hashlib.sha256(archive).hexdigest(),
                'task_commit': m.output(['git', '-C', dest, 'rev-parse', 'HEAD']),
                'files': {p: m.digest(dest / p) for p in files}, 'prepared_at': time.time(),
                'task': SPEC, 'database': db, 'validation_modes': ['check', 'test', 'mysql', 'acceptance'],
                'protected_source': ['webapp/rust/src/medium_acceptance.rs']}
    m.write_json(dest / '.local/manifest.json', manifest)
    m.write_json(m.STATE / 'trials' / (args.trial + '.json'), manifest)
    print(dest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('setup-db')
    p = sub.add_parser('prepare')
    p.add_argument('--trial', required=True)
    p.add_argument('--source', type=Path, default=m.ROOT.parent / 'practice-12')
    p.add_argument('--destination', type=Path)
    args = parser.parse_args()
    if os.environ.get('ISUSCOPE_LOCK_HELD') != '1':
        os.execvp('isuscope', ['isuscope', 'lock', '--path', str(m.ROOT / '.local/operation.lock'), '--', sys.executable, *sys.argv])
    {'setup-db': db_setup, 'prepare': prepare}[args.command](args)

if __name__ == '__main__':
    main()
