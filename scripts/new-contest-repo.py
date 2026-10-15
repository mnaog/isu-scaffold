#!/usr/bin/env python3
"""Create a contest repo from a committed scaffold revision (preparation).

The new repo gets every committed file except what config/new-repo.json excludes: the
scaffold's own conversation history, experiments and unrelated material. It starts a fresh
Git history, records the source commit in docs/preparation/scaffold-source.json, writes
docs/preparation/readiness.md from the template and runs scripts/preparation-check.py on the result.
Without --practice it also removes practice.md and the practice-only lines of AGENTS.md.
With --github OWNER/NAME it creates that private GitHub repo and pushes to it.
"""
import argparse
from datetime import datetime, timezone
import fnmatch
import json
from pathlib import Path
import subprocess
import sys

SOURCE = Path(__file__).resolve().parent.parent
PRACTICE_BLOCK_HEADING = '## 練習時のみ\n'


def git(repo, *args, data=False):
    output = subprocess.check_output(['git', '-C', str(repo), *args])
    return output if data else output.decode()


def excluded(name, config):
    if name in config['keep']:
        return False
    return any(fnmatch.fnmatch(name, pattern) for pattern in config['exclude'])


def drop_practice_block(text):
    """Remove the practice heading and its one guidance line (and the blank line after them)."""
    start = text.find(PRACTICE_BLOCK_HEADING)
    if start < 0 or text.count(PRACTICE_BLOCK_HEADING) != 1:
        raise SystemExit('AGENTS.md: expected exactly one "## 練習時のみ" block')
    lines = text[start:].split('\n')
    if len(lines) < 3 or not lines[1] or lines[2] != '':
        raise SystemExit('AGENTS.md: the practice block is not a heading, one line and a blank line')
    return text[:start] + '\n'.join(lines[3:])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('target', type=Path, help='new directory (must not exist or be empty)')
    parser.add_argument('--ref', default='HEAD', help='scaffold revision to copy (default HEAD)')
    parser.add_argument('--practice', action='store_true', help='keep practice.md and the practice block of AGENTS.md')
    parser.add_argument('--github', metavar='OWNER/NAME', help='create this private GitHub repo and push')
    args = parser.parse_args()

    target = args.target.resolve()
    if target.exists() and any(target.iterdir()):
        parser.error(f'{target} exists and is not empty')
    if target == SOURCE or SOURCE in target.parents:
        parser.error('target must be outside the scaffold')
    config = json.loads((SOURCE / 'config/new-repo.json').read_text())
    commit = git(SOURCE, 'rev-parse', '--verify', args.ref + '^{commit}').strip()
    entries = [line.split('\t', 1) for line in git(SOURCE, 'ls-tree', '-r', '-z', commit).split('\0') if line]
    files = {}
    for meta, name in entries:
        mode = meta.split()[0]
        if excluded(name, config) or (not args.practice and name in config['practice_only']):
            continue
        files[name] = (mode, git(SOURCE, 'show', f'{commit}:{name}', data=True))
    if args.ref == 'HEAD':
        dirty = git(SOURCE, 'status', '--porcelain', '--', *files).strip()
        if dirty:
            print('WARN: uncommitted scaffold changes are not copied:\n' + dirty, file=sys.stderr)

    target.mkdir(parents=True, exist_ok=True)
    for name, (mode, data) in files.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == '120000':
            path.symlink_to(data.decode())
            continue
        if name == 'AGENTS.md' and not args.practice:
            data = drop_practice_block(data.decode()).encode()
        path.write_bytes(data)
        path.chmod(0o755 if mode == '100755' else 0o644)

    created_at = datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')
    try:
        repository = git(SOURCE, 'config', '--get', 'remote.origin.url').strip()
    except subprocess.CalledProcessError:
        repository = str(SOURCE)
    preparation = target / 'docs/preparation'
    preparation.mkdir(parents=True, exist_ok=True)
    (preparation / 'scaffold-source.json').write_text(json.dumps({
        'repository': repository,
        'commit': commit,
        'created_at': created_at,
        'mode': 'practice' if args.practice else 'contest',
        'excluded': config['exclude'] + ([] if args.practice else config['practice_only']),
        'files': len(files),
    }, ensure_ascii=False, indent=2) + '\n')
    readiness = (SOURCE / config['readiness_template']).read_text()
    readiness = (readiness.replace('{{SOURCE}}', f'{repository} {commit[:12]}')
                 .replace('{{CREATED_AT}}', created_at)
                 .replace('{{MODE}}', '練習' if args.practice else '本番'))
    (preparation / 'readiness.md').write_text(readiness)

    subprocess.run(['git', 'init', '-q', '-b', 'main', str(target)], check=True)
    subprocess.run(['git', '-C', str(target), 'add', '-A'], check=True)
    subprocess.run([sys.executable, str(target / 'scripts/preparation-check.py')], check=True)
    subprocess.run(['git', '-C', str(target), 'commit', '-q', '-m',
                    f'Start from isu-scaffold {commit[:12]}\n\nCreated by scripts/new-contest-repo.py; '
                    'see docs/preparation/scaffold-source.json.'], check=True)
    print(f'created {target} from {commit[:12]} ({len(files)} files)')

    if args.github:
        subprocess.run(['gh', 'repo', 'create', args.github, '--private', '--source', str(target), '--remote', 'origin'],
                       check=True)
        # The first push carries the bundled tools (about 20 MB) in one request; with git's default
        # HTTP buffer GitHub answered 400 (practice-14-forth).
        subprocess.run(['git', '-C', str(target), '-c', 'http.postBuffer=524288000', 'push', '-u', 'origin', 'main'],
                       check=True)
        visibility = subprocess.check_output(['gh', 'repo', 'view', args.github, '--json', 'visibility',
                                              '-q', '.visibility']).decode().strip()
        if visibility != 'PRIVATE':
            sys.exit(f'{args.github} is {visibility}, not PRIVATE; fix it before adding anything')
        print(f'pushed to {args.github} (private)')
    print('next: start a new Claude Code session inside ' + str(target) + ' (benchmarks are linked to '
          'the conversation recorded there), then fill docs/preparation/readiness.md along docs/guides/preparation.md')
    return 0


if __name__ == '__main__':
    sys.exit(main())
