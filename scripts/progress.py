#!/usr/bin/env python3
"""One line per milestone of the initial phase, for the agent that keeps working meanwhile.

`make kickoff`, the build lane and the AI lanes append to .local/progress.log as
started_at, source, state, message (tab separated). An agent that runs kickoff in the
background follows it with `tail -n +1 -f .local/progress.log` instead of waiting for the
command to end. Stage timing stays in .local/stage-timing.tsv; this file only says what the
agent can do next (code imported, build settings needed, draft ready, a lane finished).
Worktrees write to the root repository's file, so every lane reports to one place.
"""
import argparse
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['emit', 'path'])
    parser.add_argument('source', nargs='?')
    parser.add_argument('state', nargs='?')
    parser.add_argument('message', nargs='*')
    args = parser.parse_args()
    if args.action == 'path':
        print(log_path())
        return
    if not args.source or not args.state:
        parser.error('emit needs SOURCE STATE [MESSAGE]')
    emit(args.source, args.state, ' '.join(args.message))


if __name__ == '__main__':
    main()
