"""A stand-in for isuscope over the index tables the scripts read and write.

install() writes an index with those tables (only the columns the scripts use) and an `isuscope` on a bin
directory that answers `isuscope sql --limit N QUERY` from it, as columns and rows, and records
`isuscope analyze RUN VERDICT --analysis TEXT [--base RUN] [--change ID --decision STATUS [--revisit TEXT]]`
into it the way isuscope does: an analysis per call (none when the latest one says the same), and a decision
that cites the run and its base.
"""
from pathlib import Path
import sqlite3

SCHEMA = """
CREATE TABLE runs (id TEXT PRIMARY KEY, started_at TEXT NOT NULL DEFAULT '', finished_at TEXT,
                   state TEXT NOT NULL DEFAULT 'complete',
                   score INTEGER, passed INTEGER, hypothesis TEXT NOT NULL DEFAULT '', config_key TEXT,
                   commit_hash TEXT, analysis_status TEXT NOT NULL DEFAULT 'not_required',
                   repeat_base_run_id TEXT, repeat_reason TEXT,
                   benchmark_started INTEGER NOT NULL DEFAULT 1);
CREATE TABLE run_bases (run_id TEXT PRIMARY KEY, base_run_id TEXT NOT NULL, set_at TEXT NOT NULL DEFAULT '');
CREATE TABLE run_tags (run_id TEXT NOT NULL, tag TEXT NOT NULL);
CREATE TABLE run_identity (run_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL);
CREATE TABLE run_analyses (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, created_at TEXT NOT NULL, verdict TEXT NOT NULL,
                           body TEXT NOT NULL, base_run_id TEXT);
CREATE TABLE changes (id TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, target TEXT);
CREATE TABLE change_decisions (id TEXT PRIMARY KEY, change_id TEXT NOT NULL, created_at TEXT NOT NULL,
                               status TEXT NOT NULL, reason TEXT NOT NULL, revisit TEXT);
CREATE TABLE change_decision_runs (decision_id TEXT NOT NULL, run_id TEXT NOT NULL);
"""

PROGRAM = """#!/usr/bin/env python3
import argparse, datetime, json, sqlite3, sys, uuid
db = sqlite3.connect(DATABASE)
if sys.argv[1:3] == ['sql', '--limit']:
    cursor = db.execute(sys.argv[4])
    print(json.dumps({'columns': [c[0] for c in cursor.description], 'rows': [list(r) for r in cursor]}))
    sys.exit(0)
if sys.argv[1:2] != ['analyze']:
    sys.exit('stub isuscope answers sql and analyze only: ' + ' '.join(sys.argv[1:]))
parser = argparse.ArgumentParser()
parser.add_argument('run')
parser.add_argument('verdict', choices=['supported', 'rejected', 'inconclusive', 'skipped'])
parser.add_argument('--base')
parser.add_argument('--analysis', required=True)
parser.add_argument('--change')
parser.add_argument('--decision', choices=['accepted', 'provisional', 'rejected', 'deferred'])
parser.add_argument('--revisit')
args = parser.parse_args(sys.argv[2:])
def resolve(run):
    rows = db.execute('SELECT id, passed, started_at FROM runs WHERE id = ? OR id LIKE ?', (run, '%' + run)).fetchall()
    if len(rows) != 1:
        sys.exit(f'run not found: {run}')
    return rows[0]
run, passed, started = resolve(args.run)
if passed != 1:
    sys.exit(f'only a PASS run can be analyzed: {run}')
if not args.analysis.strip():
    sys.exit('analysis must not be empty')
base = resolve(args.base)[0] if args.base else None
base = base or (db.execute('SELECT base_run_id FROM run_bases WHERE run_id = ?', (run,)).fetchone() or [None])[0]
base = base or (db.execute('SELECT id FROM runs WHERE started_at < ? ORDER BY started_at DESC LIMIT 1',
                           (started,)).fetchone() or [None])[0]
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
latest = db.execute('SELECT verdict, body, base_run_id FROM run_analyses WHERE run_id = ? ORDER BY created_at DESC, id DESC '
                    'LIMIT 1', (run,)).fetchone()
print(f'run       {run[-8:]}')
if latest == (args.verdict, args.analysis, base):
    print('unchanged the latest analysis already has this verdict and text')
else:
    db.execute('INSERT INTO run_analyses VALUES (?, ?, ?, ?, ?, ?)',
               (uuid.uuid4().hex, run, now, args.verdict, args.analysis, base))
db.execute('UPDATE runs SET analysis_status = ? WHERE id = ?', ('skipped' if args.verdict == 'skipped' else 'complete', run))
if args.change:
    db.execute('INSERT OR IGNORE INTO changes (id, created_at) VALUES (?, ?)', (args.change, now))
    last = db.execute('SELECT status, reason FROM change_decisions WHERE change_id = ? ORDER BY created_at DESC LIMIT 1',
                      (args.change,)).fetchone()
    if last == (args.decision, args.analysis):
        print('unchanged decision already records this status and reason')
    else:
        decision = uuid.uuid4().hex
        db.execute('INSERT INTO change_decisions VALUES (?, ?, ?, ?, ?, ?)',
                   (decision, args.change, now, args.decision, args.analysis, args.revisit))
        for cited in [run] + ([base] if base else []):
            db.execute('INSERT INTO change_decision_runs VALUES (?, ?)', (decision, cited))
    print(f'decision  {args.decision}')
db.commit()
"""


def install(directory):
    """Create the index and the program under `directory`; returns (connection, bin directory)."""
    directory = Path(directory)
    database = directory / 'isuscope-index.sqlite3'
    connection = sqlite3.connect(database)
    connection.executescript(SCHEMA)
    bin_dir = directory / 'stub-bin'
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / 'isuscope').write_text(PROGRAM.replace('DATABASE', repr(str(database)), 1))
    (bin_dir / 'isuscope').chmod(0o755)
    return connection, bin_dir
