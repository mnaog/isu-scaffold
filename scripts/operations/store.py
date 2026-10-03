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
        db.create_function('operator_context', 0, lambda: int(Path(cwd).resolve() == repo.resolve() and os.environ.get('SCAFFOLD_ROLE') == 'operator'))
        db.executescript("""
          CREATE TEMP VIEW worker_handoff AS SELECT validation,notes,deployment_requirements FROM worker_handoffs WHERE 0;
          CREATE TEMP TRIGGER publish_handoff INSTEAD OF INSERT ON worker_handoff BEGIN
            INSERT INTO worker_handoffs(task_id,base_commit,source_commit,validation,notes,deployment_requirements)
            SELECT c.task_id,COALESCE((SELECT source_commit FROM worker_handoffs WHERE task_id=c.task_id ORDER BY handoff_id DESC LIMIT 1),c.base_commit),
              c.head_commit,NEW.validation,NEW.notes,COALESCE(NEW.deployment_requirements,'[]') FROM worker_context c;
          END;
          CREATE TEMP TRIGGER own_handoff BEFORE INSERT ON main.worker_handoffs
          WHEN NOT EXISTS(SELECT 1 FROM workers w JOIN worker_context c ON w.task_id=c.task_id
                          WHERE w.task_id=NEW.task_id AND w.session_id=c.session_id AND w.worktree=c.worktree)
          BEGIN SELECT RAISE(ABORT,'publish only your own task handoff'); END;
          CREATE TEMP TRIGGER no_handoff_after_stop_ack BEFORE INSERT ON main.worker_handoffs
          WHEN EXISTS(SELECT 1 FROM worker_stops s JOIN worker_context c ON s.session_id=c.session_id
                      WHERE s.task_id=NEW.task_id AND s.acknowledged_at IS NOT NULL)
          BEGIN SELECT RAISE(ABORT,'worker already acknowledged stop'); END;
          CREATE TEMP VIEW worker_integrate AS SELECT handoff_id FROM worker_integrations WHERE 0;
          CREATE TEMP TRIGGER integrate_handoff INSTEAD OF INSERT ON worker_integrate BEGIN
            INSERT INTO worker_integrations(handoff_id,integration_commit,operator_session_id)
            SELECT NEW.handoff_id,head_commit,session_id FROM worker_context;
          END;
          CREATE TEMP TRIGGER operator_integration BEFORE INSERT ON main.worker_integrations
          WHEN operator_context()!=1
          BEGIN SELECT RAISE(ABORT,'integration receipts require the main operator'); END;
          CREATE TEMP VIEW worker_stop AS SELECT task_id,reason FROM worker_stops WHERE 0;
          CREATE TEMP TRIGGER request_worker_stop INSTEAD OF INSERT ON worker_stop BEGIN
            SELECT CASE WHEN (SELECT count(*) FROM worker_processes WHERE task_id=NEW.task_id AND exited_at IS NULL)!=1
              THEN RAISE(ABORT,'stop requires exactly one recorded running CLI') END;
            INSERT INTO worker_stop_requests(process_id,reason,requested_by)
            SELECT p.process_id,NEW.reason,c.session_id FROM worker_processes p,worker_context c
            WHERE p.task_id=NEW.task_id AND p.exited_at IS NULL;
          END;
          CREATE TEMP TRIGGER operator_stop BEFORE INSERT ON main.worker_stop_requests
          WHEN operator_context()!=1
          BEGIN SELECT RAISE(ABORT,'stop requests require the main operator'); END;
          CREATE TEMP TRIGGER worker_stop_ack_guard BEFORE UPDATE ON main.worker_stop_requests
          WHEN NEW.process_id!=OLD.process_id OR NEW.reason!=OLD.reason OR NEW.requested_by!=OLD.requested_by
            OR NEW.requested_at!=OLD.requested_at OR OLD.acknowledged_at IS NOT NULL
            OR NOT EXISTS(SELECT 1 FROM worker_processes p JOIN worker_context c ON p.session_id=c.session_id
                          JOIN workers w ON p.task_id=w.task_id
                          WHERE p.process_id=OLD.process_id AND w.worktree=c.worktree)
          BEGIN SELECT RAISE(ABORT,'only the assigned worker can acknowledge its immutable stop request'); END;
          CREATE TEMP VIEW worker_stop_ack AS SELECT request_id,acknowledgement FROM worker_stop_requests WHERE 0;
          CREATE TEMP TRIGGER acknowledge_stop INSTEAD OF INSERT ON worker_stop_ack BEGIN
            UPDATE worker_stop_requests SET acknowledged_at=strftime('%s','now'),acknowledgement=NEW.acknowledgement
            WHERE request_id=NEW.request_id;
          END;
        """)
        before = (set(r[0] for r in db.execute('SELECT handoff_id FROM worker_handoffs')),
                  set(r[0] for r in db.execute('SELECT handoff_id FROM worker_integrations')))
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
            from handoffs import validate_new
            validate_new(db, repo, cwd, before)
            db.commit()
        except Exception:
            db.rollback()
            raise
        result = rows(db, 'SELECT * FROM workers WHERE worktree=? ORDER BY started_at DESC',
                      (git(cwd, 'rev-parse', '--show-toplevel'),))
        for worker in result:
            worker['handoffs'] = rows(db, 'SELECT h.*,i.integration_commit FROM worker_handoffs h LEFT JOIN worker_integrations i USING(handoff_id) WHERE h.task_id=? ORDER BY h.handoff_id DESC', (worker['task_id'],))
            worker['stop_requests'] = rows(db, 'SELECT * FROM worker_stops WHERE task_id=? ORDER BY request_id DESC', (worker['task_id'],))
            worker['updates'] = rows(db, 'SELECT * FROM worker_updates WHERE task_id=? ORDER BY id DESC LIMIT 50',
                                     (worker['task_id'],))
        return result
