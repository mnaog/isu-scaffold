#!/usr/bin/env python3
"""One line per milestone of the kickoff, for the agent that keeps working meanwhile.

`make kickoff`, the build lane and the AI lanes append to .local/progress.log as
started_at, source, state, message (tab separated). An agent that runs kickoff in the
background follows it (`follow main --from-start`, below) instead of waiting for the
command to end. Stage timing stays in .local/stage-timing.tsv; this file only says what the
agent can do next (code imported, build settings needed, draft ready, a lane finished).
Worktrees write to the root repository's file, so every lane reports to one place.

`follow <role>` keeps reading the file and prints only the lines config/progress.json routes to that
role, each with what to do; a session follows it with Monitor and wakes only for its own lines.
"""
import argparse
import fnmatch
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent


def log_path(root=None):
    root = Path(root or ROOT)
    if os.environ.get('PROGRESS_LOG'):
        return Path(os.environ['PROGRESS_LOG'])
    try:
        common = subprocess.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'], cwd=root,
                                capture_output=True, text=True, check=True).stdout.strip()
        return Path(common).parent / '.local/progress.log'
    except (OSError, subprocess.CalledProcessError):
        return root / '.local/progress.log'


def emit(source, state, message='', root=None, path=None):
    """Never fails the caller: progress lines are a convenience, not part of the operation."""
    line = '\t'.join([time.strftime('%Y-%m-%dT%H:%M:%S%z'), source, state,
                      ' '.join(str(message).split())]) + '\n'
    try:
        path = Path(path) if path else log_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as out:
            out.write(line)
    except OSError:
        pass
    print(f'>> {source}: {state} {message}'.rstrip(), flush=True)


def load_rules(root=None):
    try:
        return json.loads((Path(root or ROOT) / 'config/progress.json').read_text()).get('rules') or []
    except (OSError, ValueError):
        return []


def route(rules, source, state):
    """{role: note} for a line: the first rule whose `lines` match "<source> <state>", else main."""
    line = f'{source} {state}'
    for rule in rules:
        if any(fnmatch.fnmatchcase(line, pattern) for pattern in rule.get('lines') or []):
            return rule.get('to') or {}
    return {'main': '宛先の表（config/progress.json）に無い行。読んで判断し、必要なら表へ足す'}


def for_role(rules, role, text):
    """The line as `follow` prints it for the role, or None when the line is not the role's."""
    fields = text.rstrip('\n').split('\t')
    if len(fields) < 3:
        return None
    at, source, state, message = fields[0], fields[1], fields[2], fields[3] if len(fields) > 3 else ''
    if source == role:
        return None
    note = route(rules, source, state).get(role)
    if note is None:
        return None
    return f'{at[11:16]} {source} {state} {message}'.rstrip() + f' → {note}'


def follow(role, root=None, from_start=False, interval=0.5, stop=None):
    """Print the role's lines as they are appended; waits for the file to appear and survives truncation."""
    path = log_path(root)
    rules = load_rules(root)
    position = None
    appeared = False
    while stop is None or not stop():
        try:
            size = path.stat().st_size
        except OSError:
            appeared = position is None
            time.sleep(interval)
            continue
        if position is None:
            # A file that appears after following started is read from its first line.
            position = 0 if from_start or appeared else size
        if size < position:
            position = 0
        if size > position:
            with path.open('rb') as handle:
                handle.seek(position)
                chunk = handle.read()
            complete = chunk[:chunk.rfind(b'\n') + 1]
            position += len(complete)
            for text in complete.decode(errors='replace').splitlines():
                if (shown := for_role(rules, role, text)) is not None:
                    print(shown, flush=True)
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['emit', 'path', 'follow'])
    parser.add_argument('source', nargs='?')
    parser.add_argument('state', nargs='?')
    parser.add_argument('message', nargs='*')
    parser.add_argument('--from-start', action='store_true', help='follow: 最初の行から読む')
    args = parser.parse_args()
    if args.action == 'path':
        print(log_path())
        return
    if args.action == 'follow':
        if args.source not in ('main', 'improve'):
            parser.error('follow needs a role: main or improve')
        try:
            follow(args.source, from_start=args.from_start)
        except KeyboardInterrupt:
            pass
        return
    if not args.source or not args.state:
        parser.error('emit needs SOURCE STATE [MESSAGE]')
    emit(args.source, args.state, ' '.join(args.message))


if __name__ == '__main__':
    main()
