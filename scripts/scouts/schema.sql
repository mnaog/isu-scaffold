PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS conversation_sources (
 agent TEXT PRIMARY KEY,
 session_id TEXT NOT NULL UNIQUE CHECK(length(trim(session_id))>0),
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
