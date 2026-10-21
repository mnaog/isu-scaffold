"""A stand-in for `isuscope sql` over the isuscope index tables the scripts read.

install() writes an index with those tables (only the columns the scripts use) and an `isuscope`
on a bin directory that answers `isuscope sql --limit N QUERY` from it, as columns and rows.
"""
from pathlib import Path
import sqlite3

SCHEMA = """
CREATE TABLE runs (id TEXT PRIMARY KEY, started_at TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'complete',
                   score INTEGER, passed INTEGER, hypothesis TEXT NOT NULL DEFAULT '', config_key TEXT,
                   benchmark_started INTEGER NOT NULL DEFAULT 1);
CREATE TABLE run_tags (run_id TEXT NOT NULL, tag TEXT NOT NULL);
CREATE TABLE run_identity (run_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL);
CREATE TABLE run_analyses (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, created_at TEXT NOT NULL, verdict TEXT NOT NULL,
                           body TEXT NOT NULL);
CREATE TABLE changes (id TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, target TEXT);
CREATE TABLE change_decisions (id TEXT PRIMARY KEY, change_id TEXT NOT NULL, created_at TEXT NOT NULL,
                               status TEXT NOT NULL, reason TEXT NOT NULL, revisit TEXT);
CREATE TABLE change_decision_runs (decision_id TEXT NOT NULL, run_id TEXT NOT NULL);
"""

PROGRAM = """#!/usr/bin/env python3
import json, sqlite3, sys
if sys.argv[1:3] != ['sql', '--limit']:
    sys.exit('stub isuscope answers sql only: ' + ' '.join(sys.argv[1:]))
cursor = sqlite3.connect(%r).execute(sys.argv[4])
print(json.dumps({'columns': [c[0] for c in cursor.description], 'rows': [list(r) for r in cursor]}))
"""


def install(directory):
    """Create the index and the program under `directory`; returns (connection, bin directory)."""
    directory = Path(directory)
    database = directory / 'isuscope-index.sqlite3'
    connection = sqlite3.connect(database)
    connection.executescript(SCHEMA)
    bin_dir = directory / 'stub-bin'
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / 'isuscope').write_text(PROGRAM % str(database))
    (bin_dir / 'isuscope').chmod(0o755)
    return connection, bin_dir
