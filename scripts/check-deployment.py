#!/usr/bin/env python3
"""Reject credentials and omitted SQL/init files before import or deployment."""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'operations'))
from handoffs import ancestor, check_manifest


def check(repo, manifest):
    items = manifest['items']
    for item in items:
        remote = item['remote'].rstrip('/')
        if remote == '/etc/mysql' or Path(remote).name == 'debian.cnf':
            raise ValueError('do not synchronize /etc/mysql wholesale or node-specific debian.cnf')
    discovery = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--path-format=absolute', '--git-common-dir'], text=True, capture_output=True)
    if discovery.returncode != 0:
        return  # Before a repository exists there are no tracked SQL dependencies.
    names = subprocess.check_output(['git', '-C', str(repo), 'ls-files', '-z'], text=True).split('\0')
    exclusions = {}
    for row in manifest.get('local_only_files', []):
        if not row.get('reason', '').strip() or not row.get('local') or row['local'] in exclusions:
            raise ValueError('local_only_files requires a unique path and an explicit reason')
        exclusions[row['local']] = row['reason']
    for name in names:
        matched = [i for i in items if name == i['local'] or (i['type'] == 'directory' and name.startswith(i['local'].rstrip('/') + '/'))]
        if Path(name).name == 'debian.cnf' and matched:
            raise ValueError('node-specific credential file included in sync: ' + name)
        if name.startswith('webapp/sql/') and Path(name).suffix in ('.sql', '.sh') and 'tests' not in Path(name).parts:
            if not matched and name not in exclusions:
                raise ValueError('SQL/init file missing from sync manifest: ' + name + '; add mapping or local_only_files reason')
    common = Path(discovery.stdout.strip()).parent
    db_path = common / '.local/operations/state.sqlite3'
    if db_path.exists():
        with sqlite3.connect('file:' + str(db_path) + '?mode=ro', uri=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='worker_integrations'").fetchone():
                for commit, requirements in db.execute('SELECT i.integration_commit,h.deployment_requirements FROM worker_integrations i JOIN worker_handoffs h USING(handoff_id)'):
                    if ancestor(repo, commit, 'HEAD'):
                        check_manifest(json.loads(requirements), manifest)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, default=Path(__file__).resolve().parent.parent)
    p.add_argument('--manifest', type=Path, required=True)
    a = p.parse_args()
    try:
        check(a.repo, json.loads(a.manifest.read_text()))
    except (ValueError, KeyError, OSError) as exc:
        sys.exit('deployment requirements rejected: ' + str(exc))


if __name__ == '__main__':
    main()
