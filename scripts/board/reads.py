"""Bounded, read-only isuscope queries for the board."""
import json
import os
from pathlib import Path
import subprocess
import time
from variance import spread

# The board reads isuscope from PATH, like every other script in the repository.
ISUSCOPE = ['isuscope']


def git(cwd, *args):
    return subprocess.check_output(['git', '-C', str(cwd), *args], text=True).strip()


def root(cwd=None):
    """The main worktree, so that every worktree shows the same runs and local records."""
    common = Path(git(cwd or Path.cwd(), 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    return common.parent


def records(value):
    """isuscope writes tables as `columns` and `rows` (value lists); give back one dict per row."""
    if isinstance(value, list):
        return [records(item) for item in value]
    if not isinstance(value, dict):
        return value
    value = {name: records(item) for name, item in value.items()}
    columns, rows = value.get('columns'), value.get('rows')
    if isinstance(columns, list) and isinstance(rows, list) and all(isinstance(row, list) for row in rows):
        table = [dict(zip(columns, row)) for row in rows]
        if set(value) == {'columns', 'rows'}:
            return table
        value.pop('columns')
        value['rows'] = table
    return value


def table_rows(value):
    """Rows of a table read by `records`: a bare list, or `rows` next to `total_count` and `truncated`."""
    return (value.get('rows') or []) if isinstance(value, dict) else (value or [])


def listed_runs(listing):
    """`list` rows, naming the short ID `short_id` as the board does for every run."""
    # `list` names runs by short ID only; isuscope takes it wherever a run ID goes.
    return [{**run, 'id': run.get('run'), 'short_id': run.get('run'), 'wait': 'wait' in (run.get('tags') or [])}
            for run in table_rows(listing.get('runs'))]


def brief_run(brief):
    """brief names its run by short ID in `run` and puts the run's details in `summary`."""
    return {'short_id': brief['run'], **(brief.get('summary') or {})}


def command_json(repo, argv, max_bytes=200000, uncapped=False):
    # isuscope trims row output to fit an AI tool limit; the board draws every requested row.
    env = {**os.environ, 'ISUSCOPE_OUTPUT_BYTES': '0'} if uncapped else None
    try:
        p = subprocess.run(argv, cwd=repo, capture_output=True, text=True, timeout=30, env=env)
        if p.returncode:
            return {'error': p.stderr.strip()[:500] or f'exit {p.returncode}', 'command': argv}
        if len(p.stdout.encode('utf-8')) > max_bytes:
            return {'error': f'取得結果が{max_bytes // 1000}KBを超えました。取得件数を減らしてください', 'command': argv}
        return json.loads(p.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return {'error': str(e)[:500], 'command': argv}


def metrics(repo, cfg, stop=None):
    """The score history and the latest finished run, refreshed in the background for the top of the board."""
    def read(argv):
        if stop is not None and stop.is_set():
            raise InterruptedError("停止されました")
        return command_json(repo, argv)
    prefix = cfg['isuscope']
    listing = records(read(prefix + ['list', '--limit', '100']))
    result = {'collected_at': time.time(), 'source': 'isuscope CLI', 'current_commit': git(repo, 'rev-parse', 'HEAD'),
              'dirty': bool(git(repo, 'status', '--porcelain')), 'latest': None, 'score_history': [], 'running': []}
    if 'error' in listing:
        result['error'] = listing['error']
        return result
    runs = listed_runs(listing)
    result['running'] = [r for r in runs if r['state'] == 'running']
    completed = [r for r in runs if r['state'] in ('complete', 'degraded', 'failed', 'aborted')]
    if not completed:
        result['error'] = '直近100件に完了runがありません'
        return result
    fields = ('id', 'short_id', 'started_at', 'score', 'passed', 'state', 'commit_hash', 'dirty', 'hypothesis',
              'analysis_status', 'wait')
    result['score_history'] = [{key: run.get(key) for key in fields} for run in reversed(completed)]
    try:
        known = spread(repo)
    except Exception:  # the history still draws without the swing
        known = {}
    for entry in result['score_history']:
        entry.update(known.get(entry['id']) or {})
    run = completed[0]
    result['latest'] = run
    result['brief'] = read(prefix + ['brief', run['id'], '--limit', '5'])
    return result
