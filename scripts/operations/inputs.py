"""Bounded, read-only isuscope queries and explicitly registered conversation history."""
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import time
from store import connect, git, rows


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
    return [{**run, 'short_id': run.get('run')} for run in table_rows(listing.get('runs'))]


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
    def read(argv):
        if stop is not None and stop.is_set():
            raise InterruptedError("停止されました")
        return command_json(repo, argv)
    prefix = cfg['isuscope']
    listing = records(read(prefix + ['list', '--limit', '100']))
    result = {'collected_at': time.time(), 'source': 'isuscope CLI', 'current_commit': git(repo, 'rev-parse', 'HEAD'),
              'dirty': bool(git(repo, 'status', '--porcelain')), 'latest': None, 'base': None, 'sections': {}, 'score_history': [], 'running': []}
    if 'error' in listing:
        result['error'] = listing['error']
        return result
    runs = listed_runs(listing)
    result['running'] = [r for r in runs if r['state'] == 'running']
    completed = [r for r in runs if r['state'] in ('complete', 'degraded', 'failed', 'aborted')]
    if not completed:
        result['error'] = '直近100件に完了runがありません'
        return result
    fields = ('id', 'short_id', 'started_at', 'score', 'passed', 'state', 'commit_hash', 'dirty', 'hypothesis', 'analysis_status')
    result['score_history'] = [{key: run.get(key) for key in fields} for run in reversed(completed)]
    run = completed[0]
    result['latest'] = run
    result['brief'] = read(prefix + ['brief', run['id'], '--limit', '5'])
    base = cfg.get('base_run')
    if base:
        brief = read(prefix + ['brief', base, '--limit', '5'])
        if 'error' in brief or (brief.get('summary') or {}).get('state') == 'running':
            result['base_error'] = brief.get('error', '比較元runが実行中です')
            base = None
        else:
            result['base'] = brief_run(brief)
            base = brief['run']
    # A run that did not record the end of initialize has only a `whole` window; brief says which.
    brief = result['brief'] if isinstance(result['brief'], dict) else {}
    database_window = (brief.get('database') or {}).get('window') or 'whole'
    series_window = (brief.get('hosts') or {}).get('window') or 'whole'
    selectors = {
        'benchmark': ['--metric-prefix', 'benchmark.'],
        'http': ['--view', 'http'],
        'sql': ['--view', 'database', '--window', database_window, '--group-by', 'sql-shape'],
        'hosts': ['--scope', 'series', '--window', series_window, '--metric-prefix', 'host.'],
    }
    for name, selector in selectors.items():
        argv = prefix + ['query', run['id'], *selector, '--limit', '8']
        if base:
            argv += ['--base', base]
        result['sections'][name] = {'command': argv, 'data': read(argv)}
    return result


def history(repo, agent, session):
    # Match both header fields, never mtime/latest-file heuristics.
    matches = []
    for path in (repo / 'docs' / 'agent-history').glob('*.md'):
        with path.open() as f:
            header = ''.join(f.readline() for _ in range(16))
        if f'- Agent: `{agent}`' in header and f'- Session: `{session}`' in header:
            with path.open('rb') as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 16000))
                tail = f.read().decode('utf-8', errors='replace')[-6000:]
            matches.append({'source': str(path.relative_to(repo)), 'session_id': session, 'tail': tail})
    return matches or [{'session_id': session, 'error': '一致するagent-historyがありません'}]


def generate(repo, cfg, stop=None):
    scenario = repo / cfg['scenario']
    if scenario.exists():
        content = scenario.read_text()
        scenario_data = {'source': cfg['scenario'], 'content': content[:12000], 'truncated': len(content) > 12000}
    else:
        scenario_data = {'source': cfg['scenario'], 'error': 'Phase 1のシナリオ整理が未作成です。推測で補わないでください'}
    with connect(repo) as db:
        sources = rows(db, 'SELECT agent,session_id FROM conversation_sources ORDER BY agent')
    conversations = {r['agent']: history(repo, r['agent'], r['session_id']) for r in sources}
    return {'generated_at': time.time(), 'repository': str(repo), 'scenario': scenario_data,
            'metrics': metrics(repo, cfg, stop), 'conversations': conversations,
            'rules': (repo / 'AGENTS.md').read_text()}


def current(repo, cfg):
    """Final scout check in the same session; no input/log/DB writes or other scouts."""
    prefix = cfg['isuscope']
    listing = records(command_json(repo, prefix + ['list', '--limit', '100']))
    completed = [r for r in listed_runs(listing)
                 if r.get('state') in ('complete', 'degraded', 'failed', 'aborted')]
    latest = completed[0] if completed else None
    database = repo / '.local' / 'operations' / 'state.sqlite3'
    sources = []
    if database.exists():
        db = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
        try:
            db.row_factory = sqlite3.Row
            sources = rows(db, 'SELECT agent,session_id FROM conversation_sources ORDER BY agent')
        finally:
            db.close()
    return {'checked_at': time.time(), 'current_commit': git(repo, 'rev-parse', 'HEAD'),
            'working_tree': git(repo, 'status', '--porcelain'),
            'diff_summary': git(repo, 'diff', 'HEAD', '--stat'),
            'latest': latest,
            'brief': command_json(repo, prefix + ['brief', latest['id'], '--limit', '5']) if latest else None,
            'metrics_error': listing.get('error') or (None if latest else '終了runがありません'),
            'conversations': {r['agent']: history(repo, r['agent'], r['session_id']) for r in sources}}


def prompt(repo, bundle):
    return (repo / 'docs' / 'roles' / 'scout-prompt.txt').read_text() + '\n\n入力:\n' + json.dumps(bundle, ensure_ascii=False, indent=2)
