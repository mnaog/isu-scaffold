"""How far a score could swing on its own, and the per-API waits, read from isuscope's run records.

A configuration is a run's `identity.values` (scripts/benchmark-identity.py: deployed files, measurement
settings, distributed build or ours, the waits table). Runs of one configuration measured more than once
(`isuscope run --repeat`) show how far the same configuration swings. Every run gets the standard
deviation, relative to each configuration's mean, pooled over all the configurations measured again
by the time it started: the swing to expect from that run's score alone.
"""
import importlib.util
import json
import math
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent


def load(repo):
    runs = []
    for path in (Path(repo) / 'isuscope-data/runs').glob('*/run.json'):
        try:
            run = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        bench = run.get('benchmark') or {}
        values = (run.get('identity') or {}).get('values') or {'commit': (run.get('source') or {}).get('commit_hash')}
        runs.append({'id': run.get('id'), 'started_at': run.get('started_at') or '', 'score': bench.get('score'),
                     'measured': run.get('state') == 'complete' and bool(bench.get('passed'))
                     and bench.get('score') is not None and not bench.get('interrupted') and not bench.get('pending'),
                     'config': json.dumps(values, sort_keys=True), 'repeat': bool(run.get('repeat')),
                     'wait': 'wait' in (run.get('tags') or [])})
    return sorted((run for run in runs if run['id']), key=lambda run: run['started_at'])


def pooled(groups):
    """Relative standard deviation pooled over configurations with two or more scores."""
    squares, freedom, runs = 0.0, 0, 0
    for scores in groups.values():
        if len(scores) < 2:
            continue
        mean = sum(scores) / len(scores)
        if not mean:
            continue
        squares += sum((score / mean - 1) ** 2 for score in scores)
        freedom += len(scores) - 1
        runs += len(scores)
    if not freedom:
        return None
    return {'sigma': math.sqrt(squares / freedom), 'groups': sum(1 for s in groups.values() if len(s) >= 2), 'runs': runs}


def spread(repo):
    """Per run ID: whether it repeated a configuration or tried a wait, and the swing known when it started."""
    result, groups = {}, {}
    for run in load(repo):
        if run['measured']:
            groups.setdefault(run['config'], []).append(run['score'])
        result[run['id']] = {'repeat': run['repeat'], 'wait': run['wait'], 'spread': pooled(groups)}
    return result


def waits(repo):
    """The waits table HEAD deploys and what was tried, from scripts/waits.py."""
    try:
        spec = importlib.util.spec_from_file_location('waits', SCRIPTS / 'waits.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        path = module.locate(Path(repo))
        table = [module.describe(rule) for rule in module.read_rules(path)]
        where = str(path.relative_to(repo))
    except (SystemExit, OSError, ValueError) as error:
        table, where = [], f'（表の場所が分からない: {error}）'
    except Exception as error:  # the board must render without the waits panel
        return {'error': str(error)[:300]}
    tried = []
    try:
        changes = module.load_changes(Path(repo))
    except Exception:
        changes = []
    for change, decisions in changes:
        latest = decisions[-1] if decisions else {}
        tried.append({'id': change.get('id'), 'status': latest.get('status') or '未判定',
                      'runs': [e.get('run_id', '')[-8:] for d in decisions for e in d.get('evidence') or []],
                      'reason': (latest.get('reason') or '')[:300], 'at': change.get('created_at')})
    return {'file': where, 'table': table, 'tried': tried}
