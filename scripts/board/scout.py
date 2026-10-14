"""The scouts' latest reports for the board, read without creating files or taking locks."""
import fcntl
import json
import sqlite3

FIELDS = ('name', 'state', 'error', 'next_at', 'posted_at', 'report', 'code_commit', 'run_id', 'base_run_id')


def configured(repo):
    """Scout names in the order of .local/scout.json, or config/scout.json without it."""
    for path in (repo / '.local' / 'scout.json', repo / 'config' / 'scout.json'):
        try:
            return list(json.loads(path.read_text()).get('scouts', {}))
        except (OSError, ValueError, AttributeError):
            continue
    return []


def daemon_running(repo):
    # The daemon holds an exclusive lock on this file while it runs; a shared try never blocks it.
    try:
        with (repo / '.local' / 'scout' / 'daemon.lock').open('rb') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass
    return False


def scouts(repo):
    result = {'running': daemon_running(repo), 'reports': [], 'error': None}
    saved = {}
    database = repo / '.local' / 'scout' / 'state.sqlite3'
    if database.exists():
        try:
            db = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2)
            try:
                db.row_factory = sqlite3.Row
                saved = {r['name']: dict(r) for r in db.execute(f"SELECT {','.join(FIELDS)} FROM scouts")}
            finally:
                db.close()
        except sqlite3.Error as exc:
            result['error'] = str(exc)[:500]
    result['reports'] = [saved.get(name) or {'name': name, 'state': 'stopped'} for name in configured(repo)]
    return result
