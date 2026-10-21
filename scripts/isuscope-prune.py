#!/usr/bin/env python3
"""Keep isuscope's local data light: drop old state-log caches, and keep isuscope-data out of backups.

isuscope turns each run's state log (about 5 MB of zstd) into a SQLite cache for `states sql` and
`states timeline` (runs/<id>/logs/.state-cache, and cache/states/<id>.sqlite3 for queries), about
40 times the log, and most of isuscope-data. They are disposable: a query on a run without one
builds it again from the saved log. The saved logs (logs/*.zst: the raw access log, the state log, the benchmark output) are the
evidence and stay.

scripts/bench-hook.py calls this when a benchmark starts, so the caches of the runs main is
reading (the latest few) stay and the rest go. A cache touched in the last ten minutes stays too,
in case a query is building or reading it. It also marks isuscope-data, once, to be skipped by
Spotlight and Time Machine (macOS): the caches and the search DB are rewritten every run and can be
rebuilt.

  python3 scripts/isuscope-prune.py [--keep N]   # from the repository root
"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import tomllib

KEEP = 3
FRESH_SECONDS = 600
RUN_ID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
NEVER_INDEX = '.metadata_never_index'


def data_dir(root):
    """isuscope's data_dir from .isuscope/config.toml (written by discover), isuscope-data if unset."""
    try:
        value = tomllib.loads((root / '.isuscope/config.toml').read_text()).get('data_dir')
    except (OSError, tomllib.TOMLDecodeError):
        value = None
    return root / (value or 'isuscope-data')


def recently_used(path, now):
    paths = [path, *path.rglob('*')] if path.is_dir() else [path]
    return any(now - p.stat().st_mtime < FRESH_SECONDS for p in paths if p.exists())


def remove(path):
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def prune(root, keep=KEEP, now=None):
    """Remove the state-log caches of all but the latest `keep` runs. Returns the removed paths."""
    now = time.time() if now is None else now
    data = data_dir(root)
    runs = data / 'runs'
    # Run ids are UUIDv7, so their names sort by start time.
    ids = sorted(p.name for p in runs.iterdir() if RUN_ID.match(p.name)) if runs.is_dir() else []
    kept = set(ids[-keep:]) if keep > 0 else set()
    candidates = [runs / run / 'logs/.state-cache' for run in ids if run not in kept]
    states = data / 'cache/states'
    if states.is_dir():
        candidates += [p for p in states.iterdir()
                       if p.is_file() and p.name.split('.', 1)[0] not in kept and RUN_ID.match(p.name.split('.', 1)[0])]
    removed = []
    for path in candidates:
        if path.exists() and not recently_used(path, now):
            remove(path)
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
    # A sticky exclusion follows the directory and needs no administrator rights.
    subprocess.run(['tmutil', 'addexclusion', str(data)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=False)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--keep', type=int, default=KEEP, help=f'runs whose caches stay (default {KEEP})')
    parser.add_argument('--root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    exclude_from_backups(args.root)
    for path in prune(args.root, args.keep):
        print(f'removed {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
