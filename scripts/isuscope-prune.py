#!/usr/bin/env python3
"""Keep isuscope's local data light: drop state-log caches isuscope no longer reads, and keep
isuscope-data out of backups.

isuscope keeps its own state-log caches (cache/states/, the most recently used runs only).
Earlier versions left one per run under runs/<id>/logs/.state-cache, about 40 times the log;
nothing reads those now, so they go. The saved logs (logs/*.zst) are the evidence and stay.

scripts/bench-hook.py calls this when a benchmark starts. It also marks isuscope-data, once, to
be skipped by Spotlight and Time Machine (macOS): the caches and the search DB are rewritten
every run and can be rebuilt.

  python3 scripts/isuscope-prune.py   # from the repository root
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

NEVER_INDEX = '.metadata_never_index'


def data_dir(root):
    """isuscope's data_dir from .isuscope/config.toml (written by discover), isuscope-data if unset."""
    try:
        value = tomllib.loads((root / '.isuscope/config.toml').read_text()).get('data_dir')
    except (OSError, tomllib.TOMLDecodeError):
        value = None
    return root / (value or 'isuscope-data')


def prune(root):
    """Remove the per-run state-log caches of earlier isuscope versions. Returns the removed paths."""
    removed = []
    for path in sorted((data_dir(root) / 'runs').glob('*/logs/.state-cache')):
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path)
    return removed


def exclude_from_backups(root):
    """Mark data_dir for Spotlight and Time Machine once (macOS only; elsewhere nothing happens)."""
    data = data_dir(root)
    marker = data / NEVER_INDEX
    if sys.platform != 'darwin' or marker.exists():
        return False
    data.mkdir(parents=True, exist_ok=True)
    marker.touch()
    # A sticky exclusion follows the directory and needs no administrator rights. tmutil takes about
    # ten seconds, and the benchmark hook that calls this should not wait for it.
    subprocess.Popen(['tmutil', 'addexclusion', str(data)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    exclude_from_backups(args.root)
    for path in prune(args.root):
        print(f'removed {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
