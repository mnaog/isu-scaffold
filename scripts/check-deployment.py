#!/usr/bin/env python3
"""Reject credentials and omitted SQL/init files before import or deployment."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

def check_requirements(requirements):
    seen = set()
    for r in requirements:
        path = r['path']
        if not path.startswith('webapp/') or '..' in Path(path).parts or path in seen:
            raise ValueError('invalid or duplicate deployment requirement path')
        seen.add(path)
        if r.get('local_only_reason', '').strip():
            continue
        if not r.get('node_group') or not r.get('remote', '').startswith('/') or '..' in Path(r['remote']).parts:
            raise ValueError('deployment requirement needs node_group and absolute remote path')
        if 'command' in r and not r['command'].strip():
            raise ValueError('migration command cannot be empty')
    return seen


def check_manifest(requirements, manifest):
    check_requirements(requirements)
    for r in requirements:
        if r.get('local_only_reason'):
            continue
        matches = []
        for item in manifest['items']:
            local = item['local'].rstrip('/')
            if item['type'] == 'file' and r['path'] == local:
                remote = item['remote']
            elif item['type'] == 'directory' and r['path'].startswith(local + '/'):
                remote = item['remote'].rstrip('/') + r['path'][len(local):]
            else:
                continue
            if remote == r['remote'] and item['node_group'] == r['node_group']:
                matches.append(item)
        if len(matches) != 1:
            raise ValueError('missing or wrong deployment mapping: ' + r['path'])
        if r.get('command') and {'node_group': r['node_group'], 'command': r['command']} not in manifest.get('post_deploy_commands', []):
            raise ValueError('missing migration post_deploy_command: ' + r['path'])


def check(repo, manifest):
    check_manifest(manifest.get('deployment_requirements', []), manifest)
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
