#!/usr/bin/env python3
"""How far the score of one configuration swings, from the runs isuscope recorded.

  spread.py [--base RUN] [--score N]

A configuration is what isuscope's identity_command reported for a run (scripts/benchmark-identity.py:
the deployed files, the measurement settings, the distributed build or ours), so a docs-only commit
is the same configuration. Without --base it is the configuration HEAD would deploy now; with
--base it is that run's.

main measures the latest accepted configuration again while nothing waits to be measured
(`isuscope run --repeat "振れ幅: ..."`), so that a proposal is judged from one run: the swing is the
largest distance of a run from the mean of its configuration, taken from the base configuration once
it was measured more than once, otherwise from the most recent configuration that was. How many
repeats are worth it is main's call.
--score places a proposal's score above, inside or below that band around the base configuration's mean.
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
    swing = max(abs(score / mean - 1) for score in scores) if mean else 0.0
    low = min(score / mean - 1 for score in scores) if mean else 0.0
    high = max(score / mean - 1 for score in scores) if mean else 0.0
    return {'runs': len(runs), 'mean': mean, 'swing': swing, 'low': low, 'high': high,
            'scores': [(r['short'], r['score']) for r in runs]}


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
    parser.add_argument('--score', type=float, help='投稿のrunの得点。振れ幅の上・内側・下のどこかを出す')
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
    base = summary(base_runs) if base_runs else None
    # The swing: the base configuration's own once it was measured again, else the latest that was.
    source = None
    if base and base['runs'] >= 2:
        source = ('基準の構成', base_runs)
    else:
        measured = [g for k, g in groups.items() if len(g) >= 2 and k != base_key]
        if measured:
            latest = max(measured, key=lambda g: g[-1]['started_at'])
            source = (f'直近に測り直した構成（最後のrun {latest[-1]["short"]}）', latest)
    width = summary(source[1]) if source else None

    verdict = None
    if args.score is not None and base and width:
        upper = base['mean'] * (1 + width['swing'])
        lower = base['mean'] * (1 - width['swing'])
        verdict = 'above' if args.score > upper else ('below' if args.score < lower else 'inside')

    if args.json:
        print(json.dumps({'base': {'how': how, 'values': base_values, **(base or {'runs': 0})},
                          'swing': None if not width else {'from': source[0], **width},
                          'score': args.score, 'verdict': verdict}, ensure_ascii=False))
        return 0

    print(f'基準: {how}（{describe(base_values)}）')
    if base:
        listed = ' '.join(f'{score:.0f}（{short}）' for short, score in base['scores'])
        print(f'  {base["runs"]}回: {listed}')
        print(f'  平均 {base["mean"]:.0f}、平均からのずれ {base["low"]:+.1%}〜{base["high"]:+.1%}')
    else:
        print('  この構成のPASSしたrunはまだ無い')
    if width:
        print(f'振れ幅: ±{width["swing"]:.1%}（{source[0]}、{width["runs"]}回）')
    else:
        print('振れ幅: まだ測っていない（測るものが無い間に、採用した構成を`isuscope run --repeat`で測り直す）')
    if base and width:
        upper = base['mean'] * (1 + width['swing'])
        lower = base['mean'] * (1 - width['swing'])
        print(f'判定の目安: {upper:.0f}より上なら上、{lower:.0f}より下なら下、その間は振れの内側（点では決めない）')
        if base['runs'] < 2:
            print('  基準の構成はまだ1回。測り直すと、平均と振れ幅がこの構成のものになる')
    if verdict:
        label = {'above': '振れ幅より上', 'inside': '振れの内側', 'below': '振れ幅より下'}[verdict]
        print(f'得点 {args.score:.0f}: {label}（基準の平均比 {args.score / base["mean"] - 1:+.1%}）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
