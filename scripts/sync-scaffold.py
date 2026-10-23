#!/usr/bin/env python3
"""Export only the declared generic files from a committed scaffold revision."""
import argparse
import fnmatch
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('target', type=Path)
    p.add_argument('--ref', default='HEAD')
    p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    source = Path(__file__).resolve().parent.parent
    target = a.target.resolve()
    if target == source:
        p.error('source and target must differ')
    commit = git(source, 'rev-parse', '--verify', a.ref + '^{commit}').decode().strip()
    manifest = json.loads(git(source, 'show', commit + ':config/scaffold-export.json'))
    # What a new contest repo leaves out (the scaffold's own tests and CI, its history) is not sent
    # to an existing one either.
    try:
        new_repo = json.loads(git(source, 'show', commit + ':config/new-repo.json'))
    except subprocess.CalledProcessError:
        new_repo = {'exclude': [], 'keep': []}
    for name in manifest['files']:
        if name not in new_repo.get('keep', []) and any(fnmatch.fnmatch(name, pattern) for pattern in new_repo['exclude']):
            p.error('config/new-repo.json leaves this out of contest repos: ' + name)
    files = {}
    removals = manifest.get('remove_files', [])
    for name in [*manifest['files'], *removals]:
        if Path(name).is_absolute() or '..' in Path(name).parts or name.startswith(('webapp/', '.local/', 'isuscope-data/', 'docs/agent-history/', 'docs/official/')):
            p.error('non-generic export path: ' + name)
        if name in removals:
            if name in manifest['files']:
                p.error('path listed for both export and removal: ' + name)
            continue
        files[name] = git(source, 'show', commit + ':' + name)
    spec = importlib.util.spec_from_file_location('preparation', source / 'scripts/preparation-check.py')
    preparation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preparation)
    errors = preparation.problems(files.items())
    if errors:
        sys.exit('non-generic export rejected:\n' + '\n'.join(errors))
    changes = [name for name, data in files.items() if not (target / name).is_file() or (target / name).read_bytes() != data]
    # A removed file may come back as a directory of the same name; only a file or link at that
    # path is left over.
    def leftover(name):
        return (target / name).is_file() or (target / name).is_symlink()
    deletions = [name for name in removals if leftover(name)]
    if a.apply and (changes or deletions):
        # Never overwrite another task's edits, including an untracked target file.
        dirty = git(target, 'status', '--porcelain', '--untracked-files=all', '--', *changes, *deletions).decode().strip()
        if dirty:
            sys.exit('export would overwrite local changes:\n' + dirty)
        for name in deletions:
            (target / name).unlink()
        for name in changes:
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[name])
            mode = git(source, 'ls-tree', commit, '--', name).decode().split()[0]
            path.chmod(0o755 if mode == '100755' else 0o644)
        receipt = {'repository': git(source, 'config', '--get', 'remote.origin.url').decode().strip(),
                   'commit': commit, 'sha256': {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}}
        path = target / 'docs/preparation/scaffold-source.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    remaining = [n for n, d in files.items() if not (target / n).is_file() or (target / n).read_bytes() != d]
    remaining += [n for n in removals if leftover(n)]
    if remaining:
        print('scaffold drift:\n' + '\n'.join(remaining))
        return 1
    print('scaffold export matches ' + commit[:12] + f' ({len(files)} files)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
