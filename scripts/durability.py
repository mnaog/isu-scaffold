#!/usr/bin/env python3
"""Relax MySQL's durability as one commit (`make durability`).

Main runs it when a run shows the database waiting on its writes (COMMIT time, redo or
binlog waits, iowait on the DB node), deploys the commit and measures it with isuscope run.
It is a separate command from `make tare` because it changes when a write reaches the disk;
nothing here changes what the application does. The settings keep every committed write
across a normal shutdown and restart, which is what the re-test does; they give up only what
an OS crash would lose.

- innodb_flush_log_at_trx_commit = 2: write redo at each commit, fsync it once a second
- skip-log-bin: no binary log (no replica, no point-in-time recovery)
- innodb_doublewrite = OFF: it only guards pages torn by an OS crash
- innodb_log_buffer_size = 64M, and a 1 GiB redo log so checkpoints stop chasing the writes.
  The option depends on the server: innodb_redo_log_capacity from MySQL 8.0.30,
  innodb_log_file_size before it and on MariaDB. With no version in the DB node's inspection
  report, the redo size is left out (an unknown option stops mysqld from starting).

The file goes into the synced MySQL directory as zz-durability.cnf, beside tare's zz-tare.cnf,
and the deploy restarts MySQL when a mysql-* item changes (the same command tare adds).
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('tare', Path(__file__).with_name('tare.py'))
tare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tare)


def server_version(root, sync, mysql_items):
    """('mysql'|'mariadb', (major, minor, patch)) from the DB node's inspection report, or None."""
    source = (mysql_items[0].get('source_node') if mysql_items else None) or sync.get('source_node')
    try:
        report = json.loads((root / '.local/inspection' / f'{source}.json').read_text())
    except (OSError, ValueError):
        return None, f'no inspection report for {source}'
    text = str((report.get('versions') or {}).get('mysql') or '')
    mariadb = re.search(r'(\d+)\.(\d+)\.(\d+)-MariaDB', text)
    if mariadb:
        return ('mariadb', tuple(map(int, mariadb.groups()))), f'{source}: MariaDB {".".join(mariadb.groups())}'
    mysql = re.search(r'Ver (\d+)\.(\d+)\.(\d+)', text)
    if mysql:
        return ('mysql', tuple(map(int, mysql.groups()))), f'{source}: MySQL {".".join(mysql.groups())}'
    return None, f'{source} reported no MySQL version ({text or "none"})'


def settings(version):
    lines = ['# make durability: commits survive a normal restart; only an OS crash can lose the last second',
             '[mysqld]', 'innodb_flush_log_at_trx_commit = 2', 'skip-log-bin', 'innodb_doublewrite = OFF',
             'innodb_log_buffer_size = 64M']
    if version is None:
        return lines, 'redo log size left unchanged: the server version is unknown'
    kind, number = version
    if kind == 'mysql' and number >= (8, 0, 30):
        lines.append('innodb_redo_log_capacity = 1G')
    elif kind == 'mysql':
        # Two files by default (innodb_log_files_in_group), resized at startup.
        lines.append('innodb_log_file_size = 512M')
    else:
        lines.append('innodb_log_file_size = 1G')
    return lines, f'redo log: {lines[-1]}'


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--no-commit', action='store_true', help='edit the files without committing them')
    args = parser.parse_args()
    root = args.root.resolve()
    sync_path = root / 'config/sync.json'
    sync = json.loads(sync_path.read_text())
    items = sync.get('items', [])
    mysql_items = [item for item in items if item.get('node_group') == 'role_mysql']
    # my.cnf reads conf.d before mysql.conf.d, so a file in mysql.conf.d wins over both.
    mysql_dir = next((item for remote in ('/etc/mysql/mysql.conf.d', '/etc/mysql/conf.d') for item in mysql_items
                      if item['type'] == 'directory' and item['remote'] == remote), None)
    if mysql_dir is None:
        print('MySQL configuration is not synced as a directory (role_mysql, /etc/mysql/mysql.conf.d or conf.d); '
              'nothing was changed', file=sys.stderr)
        return 1
    version, version_note = server_version(root, sync, mysql_items)
    lines, redo_note = settings(version)
    notes = [version_note, redo_note]
    changed = {}
    cnf = mysql_dir['local'].rstrip('/') + '/zz-durability.cnf'
    text = '\n'.join(lines) + '\n'
    if not (root / cnf).is_file() or (root / cnf).read_text() != text:
        changed[cnf] = text
    restart_mysql = {'node_group': 'role_mysql', 'command': 'sudo systemctl restart mysql', 'when': ['mysql-*']}
    for key in ('post_deploy_commands', 'rollback_commands'):
        sync[key] = tare.ensure_command(sync.get(key, []), restart_mysql,
                                        before=lambda text: 'is-active' in text and 'mysql' in text)
    sync_text = json.dumps(sync, ensure_ascii=False, indent=2) + '\n'
    if sync_text != sync_path.read_text():
        changed['config/sync.json'] = sync_text

    if not changed:
        print('durability is already applied; nothing to change')
        return 0
    dirty = tare.git(root, 'status', '--porcelain', '--', *changed).stdout.strip()
    if dirty and not args.no_commit:
        print(f'refusing to mix with uncommitted changes:\n{dirty}', file=sys.stderr)
        return 1
    for name, text in changed.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f'updated {name}')
    for note in notes:
        print(f'note: {note}')
    if args.no_commit:
        return 0
    tare.git(root, 'add', '--', *changed)
    message = ('Relax MySQL durability to a normal restart (make durability)\n\n'
               'See scripts/durability.py. Measure it as one change with isuscope run.\n\n'
               + ''.join(f'- {name}\n' for name in changed) + ''.join(f'- note: {note}\n' for note in notes))
    tare.git(root, 'commit', '-q', '-m', message)
    print(tare.git(root, 'log', '-1', '--format=%h %s').stdout.strip())
    print('next: make deploy, then isuscope run --hypothesis "..." to measure it')
    return 0


if __name__ == '__main__':
    sys.exit(main())
