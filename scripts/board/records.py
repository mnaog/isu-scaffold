"""The required records listed in config/records.json, read for the board.

Only the listed files, and files inside the listed directories, can be read. Nothing is written.
"""
import json
from pathlib import PurePosixPath
import subprocess
import threading
import time

MAX_BYTES = 1_000_000
MAX_FILES = 200
CACHE_SECONDS = 10


def declared(repo):
    return json.loads((repo / 'config/records.json').read_text())['records']


def git_lines(repo, *args):
    try:
        return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True,
                              timeout=10).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return []


def last_commit(repo, path):
    line = next(iter(git_lines(repo, 'log', '-1', '--format=%h%x09%ct%x09%s', '--', path)), '')
    parts = line.split('\t', 2)
    if len(parts) != 3:
        return None
    return {'commit': parts[0], 'committed_at': int(parts[1]), 'subject': parts[2]}


def inside(repo, path):
    """The file under the repository that `path` names, or None when it leaves the repository."""
    relative = PurePosixPath(path)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        return None
    target = (repo / relative).resolve()
    return target if target.is_relative_to(repo.resolve()) else None


def files_in(repo, directory):
    found = []
    for path in sorted(directory.rglob('*')):
        if len(found) >= MAX_FILES:
            break
        if path.is_file() and not path.name.startswith('.') and inside(repo, path.relative_to(repo).as_posix()):
            stat = path.stat()
            found.append({'path': path.relative_to(repo).as_posix(), 'size': stat.st_size, 'modified_at': stat.st_mtime})
    return found


def listing(repo):
    records = []
    paths = [record['path'] for record in declared(repo)]
    # One status call for every path: which records have changes that are not committed yet.
    changed = git_lines(repo, 'status', '--porcelain', '--untracked-files=all', '--', *paths)
    changed_paths = [line[3:].split(' -> ')[-1] for line in changed]
    for record in declared(repo):
        path = record['path']
        target = inside(repo, path.rstrip('/'))
        item = {**record, 'exists': bool(target and target.exists()), 'directory': path.endswith('/'),
                'uncommitted': any(c.startswith(path) if path.endswith('/') else c == path for c in changed_paths),
                'last_commit': last_commit(repo, path)}
        if item['exists'] and item['directory']:
            item['files'] = files_in(repo, target)
            item['exists'] = bool(item['files'])
            item['modified_at'] = max((f['modified_at'] for f in item['files']), default=None)
        elif item['exists']:
            stat = target.stat()
            item.update(size=stat.st_size, modified_at=stat.st_mtime)
        records.append(item)
    return {'collected_at': time.time(), 'records': records}


def readable(repo, path):
    """A listed file, or a file inside a listed directory."""
    for record in declared(repo):
        allowed = record['path']
        if path == allowed or (allowed.endswith('/') and path.startswith(allowed)):
            return inside(repo, path)
    return None


def content(repo, path):
    target = readable(repo, path)
    if target is None:
        raise ValueError('not a path listed in config/records.json')
    if not target.is_file():
        return {'path': path, 'error': 'まだ作られていません'}
    raw = target.read_bytes()
    if b'\0' in raw[:8192]:
        return {'path': path, 'error': 'テキストではないため表示できません'}
    text = raw[:MAX_BYTES].decode('utf-8', errors='replace')
    return {'path': path, 'text': text, 'truncated': len(raw) > MAX_BYTES, 'size': len(raw),
            'modified_at': target.stat().st_mtime, 'last_commit': last_commit(repo, path)}


class ListingCache:
    """The board polls; git is asked at most every CACHE_SECONDS."""

    def __init__(self):
        self.lock = threading.Lock()
        self.value = None
        self.at = 0.0

    def get(self, repo):
        with self.lock:
            if self.value is None or time.monotonic() - self.at >= CACHE_SECONDS:
                try:
                    self.value = listing(repo)
                except (OSError, ValueError, KeyError) as exc:
                    self.value = {'error': f'config/records.jsonを読めません: {exc}', 'records': []}
                self.at = time.monotonic()
            return self.value
