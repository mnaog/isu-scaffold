"""Shared local state. No dependency on the current worktree's .local directory."""
import contextlib
import fcntl
import json
import os
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


def launch_context(cwd):
    worktree = Path(git(cwd, 'rev-parse', '--show-toplevel'))
    path = worktree / '.local/worker-context.json'
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if (value['worktree'] != str(worktree)
            or value['branch'] != git(cwd, 'branch', '--show-current')
            or value['mode'] != git(cwd, 'config', f"branch.{value['branch']}.scaffold-mode")
            or value['purpose'] != git(cwd, 'config', f"branch.{value['branch']}.description")
            or not value['parent_session_id']):
        raise ValueError('worker launch context does not match this lane')
    return value


def worker_context(db, cwd):
    worktree = git(cwd, 'rev-parse', '--show-toplevel')
    branch = git(cwd, 'branch', '--show-current')
    head = git(cwd, 'rev-parse', 'HEAD')
    try:
        base = git(cwd, 'config', f'branch.{branch}.scaffold-base')
    except subprocess.CalledProcessError:
        base = git(cwd, 'merge-base', 'HEAD', os.environ.get('SCAFFOLD_BASE_REF', 'main'))
    base = git(cwd, 'rev-parse', os.environ.get('SCAFFOLD_BASE_COMMIT', base) + '^{commit}')
    session = os.environ.get('SCAFFOLD_SESSION_ID') or os.environ.get('CODEX_THREAD_ID', '')
    parent = os.environ.get('SCAFFOLD_PARENT_SESSION_ID', '')
    launch = launch_context(cwd)
    if launch:
        if parent and parent != launch['parent_session_id']:
            raise ValueError('parent session ID conflicts with explicit launch context')
        parent = launch['parent_session_id']
    task_id = os.environ.get('SCAFFOLD_TASK_ID', '')
    if not task_id:
        found = db.execute("SELECT task_id FROM workers WHERE worktree=? AND state IN ('working','blocked','developed')", (worktree,)).fetchone()
        task_id = found[0] if found else ''
    db.execute('CREATE TEMP TABLE worker_context(task_id, parent_session_id, session_id, agent, worktree, branch, base_commit, head_commit, lane_mode, lane_purpose)')
    db.execute('INSERT INTO worker_context VALUES (?,?,?,?,?,?,?,?,?,?)',
               (task_id, parent, session, os.environ.get('SCAFFOLD_AGENT') or ('codex' if launch else 'unknown'), worktree, branch, base, head,
                launch['mode'] if launch else '', launch['purpose'] if launch else ''))
    db.executescript('''
      CREATE TEMP VIEW worker_start AS SELECT task, estimate_minutes, completion_criteria, planned_validation FROM workers WHERE 0;
      CREATE TEMP TRIGGER record_start INSTEAD OF INSERT ON worker_start BEGIN
        INSERT INTO workers(task_id,task,estimate_minutes,completion_criteria,planned_validation,
          parent_session_id,session_id,agent,worktree,branch,base_commit)
        SELECT COALESCE(NULLIF(task_id,''),lower(hex(randomblob(16)))),COALESCE(NULLIF(lane_purpose,''),NEW.task),NEW.estimate_minutes,NEW.completion_criteria,NEW.planned_validation,
          parent_session_id,session_id,agent,worktree,branch,base_commit FROM worker_context;
        UPDATE worker_context SET task_id=(SELECT task_id FROM workers WHERE rowid=last_insert_rowid());
      END;
      CREATE TEMP TRIGGER protect_other_worker_notes BEFORE UPDATE OF notes ON main.workers
      WHEN NEW.notes!=OLD.notes AND OLD.worktree!=(SELECT worktree FROM worker_context)
      BEGIN SELECT RAISE(ABORT,'other worker notes are read-only; use worker_updates for observations, or create a new worktree for another task'); END;
      CREATE TEMP VIEW worker_inbox AS
        SELECT u.* FROM worker_updates u JOIN workers w ON w.task_id=u.task_id
        WHERE w.worktree=(SELECT worktree FROM worker_context) ORDER BY u.id;
    ''')
    return worktree


def worker_sql(repo, cwd, sql):
    with connect(repo) as db:
        worker_context(db, cwd)
        # SQL is intentionally the interface; inserts/updates are an atomic unit.
        try:
            db.executescript('BEGIN IMMEDIATE;\n' + sql)
            launch = launch_context(cwd)
            if launch:
                context = db.execute('SELECT * FROM worker_context').fetchone()
                if not context['session_id']:
                    raise ValueError('worker real session ID is required')
                existing = db.execute('SELECT * FROM worker_processes WHERE process_id=?', (launch['process_id'],)).fetchone()
                if existing and (existing['session_id'] != context['session_id'] or existing['pid'] != launch['pid']):
                    raise ValueError('worker process identity conflicts with launch context')
                db.execute('''INSERT INTO worker_processes(process_id,task_id,session_id,pid)
                    VALUES (?,?,?,?) ON CONFLICT(process_id) DO UPDATE SET task_id=excluded.task_id''',
                    (launch['process_id'], context['task_id'] or None, context['session_id'], launch['pid']))
            db.commit()
        except Exception:
            db.rollback()
            raise
        result = rows(db, 'SELECT * FROM workers WHERE worktree=? ORDER BY started_at DESC',
                      (git(cwd, 'rev-parse', '--show-toplevel'),))
        for worker in result:
            worker['updates'] = rows(db, 'SELECT * FROM worker_updates WHERE task_id=? ORDER BY id DESC LIMIT 50',
                                     (worker['task_id'],))
        return result
