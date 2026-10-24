#!/usr/bin/env python3
"""Which sync items a deploy must send, and which commands it must run for them.

  deploy-plan.py changed  --manifest M --previous COMMIT --head COMMIT   item names, one per line
  deploy-plan.py commands --manifest M --key post_deploy_commands --changed FILE   group<TAB>command

An item changes when its committed content, its definition in sync.json, or what builds it
(its local_builds entry, scripts/local-build.py) differs from the
last successful deploy. Without a recorded deploy, or with DEPLOY_ALL=1, every item changes:
the first deploy after the distributed baseline must replace the distributed binary.

A command object may name the items it serves in "when" (fnmatch patterns such as "nginx-*").
It runs only when one of them changed, so a Go-only change neither restarts MySQL nor
reloads nginx. Commands without "when" (strings, or objects without the key) always run.
"""
import argparse
import fnmatch
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def git(*args):
    result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def object_ids(specs):
    """The object each `commit:path` names, or None where it does not exist, in one git call."""
    if not specs:
        return {}
    result = subprocess.run(['git', 'cat-file', '--batch-check=%(objectname)'], cwd=ROOT, capture_output=True,
                            text=True, input=''.join(f'{spec}\n' for spec in specs))
    lines = result.stdout.splitlines()
    if result.returncode != 0 or len(lines) != len(specs):
        return {spec: git('rev-parse', spec) for spec in specs}
    return {spec: None if line.endswith((' missing', ' ambiguous')) else line for spec, line in zip(specs, lines)}


def relative(manifest):
    path = Path(manifest).resolve()
    try:
        return path.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return None


def changed_items(manifest_path, previous, head):
    manifest = json.loads(Path(manifest_path).read_text())
    names = [item['name'] for item in manifest['items']]
    if not previous or os.environ.get('DEPLOY_ALL') == '1':
        return names
    manifest_relative = relative(manifest_path)
    old_text = git('show', f'{previous}:{manifest_relative}') if manifest_relative else None
    if old_text is None:
        return names
    try:
        old = json.loads(old_text)
    except ValueError:
        return names
    old_items = {item['name']: item for item in old.get('items', [])}
    old_builds = {build['item']: build for build in old.get('local_builds', [])}
    builds = {build['item']: build for build in manifest.get('local_builds', [])}
    paths = [item['local'] for item in manifest['items']] + ['scripts/local-build.py']
    ids = object_ids([f'{commit}:{path}' for path in paths for commit in (previous, head)])
    changed = []
    for item in manifest['items']:
        name = item['name']
        build = builds.get(name)
        same = (old_items.get(name) == item and old_builds.get(name) == build
                and ids[f'{previous}:{item["local"]}'] == ids[f'{head}:{item["local"]}'])
        if same and build:
            same = ids[f'{previous}:scripts/local-build.py'] == ids[f'{head}:scripts/local-build.py']
        if not same:
            changed.append(name)
    return changed


def selected_commands(manifest_path, key, changed_file):
    manifest = json.loads(Path(manifest_path).read_text())
    changed = [line for line in Path(changed_file).read_text().split() if line]
    for command in manifest.get(key, []) or []:
        if isinstance(command, str):
            yield 'application', command
            continue
        when = command.get('when')
        if when is None or any(fnmatch.fnmatchcase(name, pattern) for pattern in when for name in changed):
            yield command['node_group'], command['command']


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['changed', 'commands'])
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--previous', default='')
    parser.add_argument('--head', default='HEAD')
    parser.add_argument('--key')
    parser.add_argument('--changed')
    args = parser.parse_args()
    if args.action == 'changed':
        print('\n'.join(changed_items(args.manifest, args.previous, args.head)))
        return
    if not args.key or not args.changed:
        parser.error('commands needs --key and --changed')
    for group, command in selected_commands(args.manifest, args.key, args.changed):
        sys.stdout.write(f'{group}\t{command}\n')


if __name__ == '__main__':
    main()
