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

-- Research publication is durable before any external process starts.
CREATE TABLE IF NOT EXISTS research_proposals (
 proposal_id TEXT NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>0),
 digest TEXT NOT NULL,
 body TEXT NOT NULL,
 published_at REAL NOT NULL,
 PRIMARY KEY(proposal_id,revision)
);
CREATE TABLE IF NOT EXISTS research_jobs (
 job_id TEXT PRIMARY KEY,
 kind TEXT NOT NULL CHECK(kind IN ('researcher','codex','claude')),
 proposal_id TEXT,
 revision INTEGER,
 input TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','running','complete','failed')),
 attempt_id TEXT,
 owner_pid INTEGER,
 owner_birth TEXT,
 error TEXT,
 result TEXT,
 created_at REAL NOT NULL,
 FOREIGN KEY(proposal_id,revision) REFERENCES research_proposals(proposal_id,revision),
 UNIQUE(proposal_id,revision,kind)
);
CREATE TABLE IF NOT EXISTS research_attempts (
 attempt_id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL REFERENCES research_jobs(job_id),
 started_at REAL NOT NULL,
 finished_at REAL,
 state TEXT NOT NULL,
 error TEXT,
 result TEXT,
 artifact_ref TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_service (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 enabled INTEGER NOT NULL DEFAULT 1,
 pid INTEGER,
 birth TEXT
);
INSERT OR IGNORE INTO research_service(singleton) VALUES(1);
CREATE TRIGGER IF NOT EXISTS proposal_immutable BEFORE UPDATE ON research_proposals
 BEGIN SELECT RAISE(ABORT,'published proposals are immutable; use a new revision'); END;
