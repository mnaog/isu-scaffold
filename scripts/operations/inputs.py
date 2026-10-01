"""Bounded, read-only isuscope queries and explicitly registered operator history."""
import json
from pathlib import Path
import subprocess
import time
from store import connect, git, rows


def command_json(repo, argv):
    try:
        p = subprocess.run(argv, cwd=repo, capture_output=True, text=True, timeout=30)
        if p.returncode:
            return {'error': p.stderr.strip()[:500] or f'exit {p.returncode}', 'command': argv}
        if len(p.stdout) > 200000:
            return {'error': '取得結果が200KBを超えました。selectorを絞ってください', 'command': argv}
        return json.loads(p.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return {'error': str(e)[:500], 'command': argv}


def metrics(repo, cfg, stop=None):
    def read(argv):
        if stop is not None and stop.is_set():
            raise InterruptedError("停止されました")
        return command_json(repo, argv)
    prefix = cfg['isuscope']
    listing = read(prefix + ['list', '--limit', '100'])
    result = {'collected_at': time.time(), 'source': 'isuscope CLI', 'current_commit': git(repo, 'rev-parse', 'HEAD'),
              'dirty': bool(git(repo, 'status', '--porcelain')), 'latest': None, 'base': None, 'sections': {}, 'score_history': []}
    if 'error' in listing:
        result['error'] = listing['error']
        return result
    completed = [r for r in listing.get('runs', []) if r['state'] in ('complete', 'degraded', 'failed', 'aborted')]
    if not completed:
        result['error'] = '直近100件に完了runがありません'
        return result
    fields = ('id', 'short_id', 'started_at', 'score', 'passed', 'state', 'commit_hash', 'dirty')
    result['score_history'] = [{key: run.get(key) for key in fields} for run in reversed(completed)]
    run = completed[0]
    result['latest'] = run
    result['brief'] = read(prefix + ['brief', run['id'], '--limit', '5'])
    base = cfg.get('base_run')
    if base:
        brief = read(prefix + ['brief', base, '--limit', '5'])
        if 'error' in brief or brief.get('run', {}).get('state') == 'running':
            result['base_error'] = brief.get('error', '比較元runが実行中です')
            base = None
        else:
            result['base'] = brief['run']
            base = brief['run']['id']
    selectors = {
        'benchmark': ['--metric-prefix', 'benchmark.'],
        'http': ['--view', 'http'],
        'sql': ['--view', 'database', '--window', 'load', '--group-by', 'sql-shape'],
        'hosts': ['--scope', 'series', '--window', 'load', '--metric-prefix', 'host.'],
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
        workers = rows(db, "SELECT * FROM workers WHERE state IN ('working','blocked','developed') ORDER BY started_at")
        operators = rows(db, 'SELECT agent,session_id FROM operators ORDER BY agent')
    conversations = {r['agent']: history(repo, r['agent'], r['session_id']) for r in operators}
    for agent in ('claude', 'codex'):
        conversations.setdefault(agent, [{'error': 'operatorセッション未登録'}])
    return {'generated_at': time.time(), 'repository': str(repo), 'scenario': scenario_data,
            'metrics': metrics(repo, cfg, stop), 'workers': workers, 'operators': conversations,
            'rules': (repo / 'AGENTS.md').read_text()}


def prompt(repo, bundle):
    return (repo / 'docs' / 'roles' / 'scout-prompt.txt').read_text() + '\n\n入力:\n' + json.dumps(bundle, ensure_ascii=False, indent=2)
