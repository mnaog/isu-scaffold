"""Shared local state. No dependency on the current worktree's .local directory."""
import contextlib
import fcntl
import json
from pathlib import Path
import sqlite3
import subprocess

HERE = Path(__file__).resolve().parent


def git(cwd, *args):
    return subprocess.check_output(['git', '-C', str(cwd), *args], text=True).strip()


def root(cwd=None):
    common = Path(git(cwd or Path.cwd(), 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    return common.parent


def local(repo):
    p = repo / '.local' / 'operations'
    p.mkdir(parents=True, exist_ok=True)
    return p


@contextlib.contextmanager
def connect(repo):
    db = sqlite3.connect(local(repo) / 'state.sqlite3', timeout=15)
    db.row_factory = sqlite3.Row
    db.executescript((HERE / 'schema.sql').read_text())
    try:
        with db:
            yield db
    finally:
        db.close()


def config(repo):
    path = repo / '.local' / 'operations.json'
    if not path.exists():
        path = repo / 'config' / 'operations.json'
    value = json.loads(path.read_text())
    for key in ('interval_seconds', 'timeout_seconds', 'retry_max_seconds'):
        if value[key] <= 0:
            raise ValueError(f'{key} must be positive')
    return value


@contextlib.contextmanager
def lock(repo, name, blocking=False):
    # Advisory locks are released by the OS even after a crash. Never unlink these files.
    with (local(repo) / (name + '.lock')).open('a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            raise RuntimeError(f'{name}: already running') from None
        try:
            yield handle
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def rows(db, sql, args=()):
    return [dict(r) for r in db.execute(sql, args)]
