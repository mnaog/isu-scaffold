#!/usr/bin/env python3
"""The scores of one configuration, from the runs isuscope recorded.

  spread.py [--base RUN]

A configuration is what isuscope's identity_command reported for a run (scripts/benchmark-identity.py:
the deployed files, the measurement settings, the distributed build or ours, the waits), so a docs-only
commit is the same configuration. Without --base it is the configuration HEAD would deploy now; with
--base it is that run's.

main measures the latest accepted configuration again while nothing waits to be measured
(`isuscope run --repeat "振れ幅: ..."`). This lists that configuration's scores, their mean and how far
they stray from it, and, until it was measured more than once, the same for the most recent
configuration that was.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def root_repo():
    """Runs live in the root repository even when a worktree calls this script."""
    try:
        common = subprocess.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'], cwd=ROOT,
                                capture_output=True, text=True, check=True).stdout.strip()
        return Path(common).parent
    except (OSError, subprocess.CalledProcessError):
        return ROOT


def key(values):
    return json.dumps(values or {}, sort_keys=True)


def load_runs(root):
    runs = []
    for path in sorted((root / 'isuscope-data/runs').glob('*/run.json')):
        try:
            run = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        bench = run.get('benchmark') or {}
        if run.get('state') != 'complete' or not bench.get('passed') or bench.get('score') is None:
            continue
        if bench.get('interrupted') or bench.get('pending'):
            continue
        values = (run.get('identity') or {}).get('values')
        if not values:  # recorded before identity_command existed: fall back to the commit
            values = {'commit': (run.get('source') or {}).get('commit_hash')}
        runs.append({'id': run['id'], 'short': run['id'][-8:], 'started_at': run.get('started_at') or '',
                     'score': bench['score'], 'mode': run.get('mode'), 'config': key(values), 'values': values,
                     'repeat': bool(run.get('repeat'))})
    runs.sort(key=lambda r: r['started_at'])
    return runs


def current_values(root):
    script = root / 'scripts/benchmark-identity.py'
    result = subprocess.run([sys.executable, str(script)], cwd=root, capture_output=True, text=True)
    if result.returncode:
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


def summary(runs):
    scores = [r['score'] for r in runs]
    mean = sum(scores) / len(scores)
    low = min(score / mean - 1 for score in scores) if mean else 0.0
    high = max(score / mean - 1 for score in scores) if mean else 0.0
    return {'runs': len(runs), 'mean': mean, 'low': low, 'high': high,
            'scores': [(r['short'], r['score']) for r in runs]}


def lines(runs):
    stats = summary(runs)
    listed = ' '.join(f'{score:.0f}（{short}）' for short, score in stats['scores'])
    return [f'  {stats["runs"]}回: {listed}',
            f'  平均 {stats["mean"]:.0f}、平均からのずれ {stats["low"]:+.1%}〜{stats["high"]:+.1%}']


def by_config(runs):
    groups = {}
    for run in runs:
        groups.setdefault(run['config'], []).append(run)
    return groups


def describe(values):
    return '、'.join(f'{k} {str(v)[:8]}' for k, v in sorted(values.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--base', help='基準の構成のrun（短縮IDかrun ID）。無ければHEADが配る構成')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    root = root_repo()
    runs = load_runs(root)
    if not runs:
        raise SystemExit('PASSしたrunがまだ無い')
    groups = by_config(runs)
    if args.base:
        match = [r for r in runs if r['id'] == args.base or r['id'].endswith(args.base)]
        if len(match) != 1:
            raise SystemExit(f'run {args.base} が見つからない（PASSしたrunの短縮IDを渡す）')
        base_key, base_values, how = match[0]['config'], match[0]['values'], f'run {match[0]["short"]}の構成'
    else:
        values = current_values(root)
        if values is None:
            base_key, base_values, how = runs[-1]['config'], runs[-1]['values'], f'最後のrun {runs[-1]["short"]}の構成（HEADの構成を出せなかった）'
        else:
            base_key, base_values, how = key(values), values, 'HEADが配る構成'

    base_runs = groups.get(base_key, [])
    # Until the base configuration was measured again, the latest configuration that was.
    other = None
    if len(base_runs) < 2:
        measured = [g for k, g in groups.items() if len(g) >= 2 and k != base_key]
        if measured:
            other = max(measured, key=lambda g: g[-1]['started_at'])

    if args.json:
        print(json.dumps({'base': {'how': how, 'values': base_values, **(summary(base_runs) if base_runs else {'runs': 0})},
                          'other': None if not other else {'last_run': other[-1]['short'], 'values': other[-1]['values'],
                                                           **summary(other)}}, ensure_ascii=False))
        return 0

    print(f'{how}（{describe(base_values)}）')
    print('\n'.join(lines(base_runs)) if base_runs else '  この構成のPASSしたrunはまだ無い')
    if other:
        print(f'直近に2回以上測った構成（最後のrun {other[-1]["short"]}、{describe(other[-1]["values"])}）')
        print('\n'.join(lines(other)))
    elif len(base_runs) < 2:
        print('同じ構成を2回以上測ったrunはまだ無い')
    return 0


if __name__ == '__main__':
    sys.exit(main())
