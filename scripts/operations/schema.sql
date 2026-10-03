PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS workers (
 task_id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
 task TEXT NOT NULL CHECK(length(trim(task))>0),
 estimate_minutes REAL NOT NULL CHECK(estimate_minutes>0),
 remaining_minutes REAL CHECK(remaining_minutes>=0),
 completion_criteria TEXT NOT NULL CHECK(length(trim(completion_criteria))>0),
 planned_validation TEXT NOT NULL CHECK(length(trim(planned_validation))>0),
 state TEXT NOT NULL DEFAULT 'working' CHECK(state IN ('working','blocked','developed','integrated','dismissed')),
 parent_session_id TEXT NOT NULL CHECK(length(parent_session_id)>0),
 session_id TEXT NOT NULL CHECK(length(session_id)>0),
 agent TEXT NOT NULL,
 worktree TEXT NOT NULL,
 branch TEXT NOT NULL,
 base_commit TEXT NOT NULL CHECK(length(base_commit)=40),
 started_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
 updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
 completed_at INTEGER,
 integrated_at INTEGER,
 result_commit TEXT,
 integration_commit TEXT,
 validation TEXT,
 notes TEXT NOT NULL DEFAULT '',
 CHECK(state NOT IN ('developed','integrated') OR
   (result_commit IS NOT NULL AND length(result_commit)=40 AND validation IS NOT NULL AND length(trim(validation))>0 AND completed_at IS NOT NULL)),
 CHECK(state!='integrated' OR (integration_commit IS NOT NULL AND length(integration_commit)=40 AND integrated_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS worker_active ON workers(worktree)
 WHERE state IN ('working','blocked','developed');
CREATE TRIGGER IF NOT EXISTS worker_update_time AFTER UPDATE ON workers
 WHEN NEW.updated_at=OLD.updated_at
 BEGIN UPDATE workers SET updated_at=strftime('%s','now') WHERE task_id=NEW.task_id; END;
CREATE TABLE IF NOT EXISTS worker_processes (
 process_id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
 task_id TEXT REFERENCES workers(task_id),
 session_id TEXT NOT NULL,
 pid INTEGER NOT NULL,
 started_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
 exited_at INTEGER,
 exit_code INTEGER
);
CREATE TABLE IF NOT EXISTS operators (
 agent TEXT PRIMARY KEY CHECK(agent IN ('codex','claude')),
 session_id TEXT NOT NULL UNIQUE,
 registered_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
-- Observations and integration decisions do not change a worker's assignment.
CREATE TABLE IF NOT EXISTS worker_updates (
 id INTEGER PRIMARY KEY,
 task_id TEXT NOT NULL REFERENCES workers(task_id),
 kind TEXT NOT NULL CHECK(kind IN ('observation','integration','dismissal')),
 body TEXT NOT NULL CHECK(length(trim(body))>0),
 run_id TEXT,
 commit_hash TEXT,
 created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
 CHECK(kind!='observation' OR
   (run_id IS NOT NULL AND length(run_id)>0 AND commit_hash IS NOT NULL AND length(commit_hash)=40))
);
CREATE TRIGGER IF NOT EXISTS worker_assignment_immutable
 BEFORE UPDATE OF task,worktree,branch,parent_session_id,session_id,base_commit ON workers
 WHEN NEW.task!=OLD.task OR NEW.worktree!=OLD.worktree OR NEW.branch!=OLD.branch
   OR NEW.parent_session_id!=OLD.parent_session_id OR NEW.session_id!=OLD.session_id
   OR NEW.base_commit!=OLD.base_commit
 BEGIN SELECT RAISE(ABORT,'worker assignment is fixed; create a separate worktree for a new task'); END;
CREATE TABLE IF NOT EXISTS scouts (
 name TEXT PRIMARY KEY,
 state TEXT NOT NULL DEFAULT 'stopped',
 error TEXT,
 next_at REAL,
 started_at REAL,
 posted_at REAL,
 report TEXT CHECK(report IS NULL OR (length(trim(report)) BETWEEN 1 AND 300)),
 input_ref TEXT,
 code_commit TEXT,
 run_id TEXT,
 base_run_id TEXT,
 session_id TEXT,
 failures INTEGER NOT NULL DEFAULT 0
);

CREATE TRIGGER IF NOT EXISTS worker_integrate_guard BEFORE UPDATE OF state ON workers
 WHEN NEW.state='integrated' AND OLD.state NOT IN ('developed','integrated')
 BEGIN SELECT RAISE(ABORT,'development must complete before integration'); END;

-- Enforce the current worker policy for new tasks, preserving historical rows.
CREATE TRIGGER IF NOT EXISTS worker_codex_only BEFORE INSERT ON workers
 WHEN NEW.agent!='codex'
 BEGIN SELECT RAISE(ABORT,'worker must use Codex'); END;
