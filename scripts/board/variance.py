"""How far a score could swing on its own, and the per-API waits, read from isuscope's run records.

isuscope groups runs by configuration (`config_key`: what was deployed, the node fingerprints, the
measurement settings) and `isuscope variance` gives every run the standard deviation, relative to
each configuration's mean, pooled over the configurations measured again by the time it started
(older configurations weigh less): the swing to expect from that run's score alone.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

SCRIPTS = Path(__file__).resolve().parent.parent


def spread(repo):
    """Per short run ID: its configuration, whether it was a --repeat, and the swing known when it started."""
    output = subprocess.run(['isuscope', 'variance', '--json'], cwd=repo, capture_output=True, text=True,
                            timeout=30, check=True, env={**os.environ, 'ISUSCOPE_OUTPUT_BYTES': '0'}).stdout
    result = {}
    table = json.loads(output).get('runs') or {}
    for row in (dict(zip(table['columns'], values)) for values in table.get('rows') or []):
        known = row.get('spread')
        result[row['run']] = {'config': row.get('config_key'), 'repeat': bool(row.get('repeat')),
                              'spread': None if known is None else
                              {'sigma': known, 'groups': row.get('spread_configs'), 'runs': row.get('spread_runs'),
                                       'effective': row.get('spread_effective_configs')}}
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
                      'runs': [run[-8:] for d in decisions for run in d['runs']],
                      'reason': (latest.get('reason') or '')[:300], 'at': change.get('created_at')})
    return {'file': where, 'table': table, 'tried': tried}
