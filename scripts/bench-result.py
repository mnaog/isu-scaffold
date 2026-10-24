#!/usr/bin/env python3
"""The two ends of `make bench` (scripts/bench.sh) for main: the check before the deploy, and the facts main
decides on when the run ends, delivered on the Monitor it already follows.

  bench-result.py check                                   exit 3 while a PASS run still waits for its analysis
  bench-result.py report RUN --brief FILE [--output FILE]  the run ended: the facts, then its brief
  bench-result.py not-started REASON [--output FILE]       no run came of it (the deploy, isuscope run)

`report` and `not-started` write .local/bench-results/<name>.txt, print it, and write a progress line
(`benchmark finished`, `benchmark failed` or `benchmark not-started`, scripts/progress.py) whose first word is
<name>; config/progress.json routes it to main with that file attached. So main reads the result on its Monitor
instead of reading the background command's output first (thirteenth's main did that on all of its 90 cycles,
since the notice of a finished background command carries only the path of its output).

The facts are what the brief does not say, and decide nothing: the proposals the run measured with what each
expects, the spread of the base run's own configuration (the brief's spread is of the configuration measured
twice most recently, which may be another), the next candidates with their prebuild, and the runs whose
detailed analysis is still owed. A run is reported only by the ID `isuscope run` printed: when it printed none,
the last run's brief would read as this one's.

`check` refuses before the deploy: isuscope refuses the run while a PASS run waits for an analysis, but only
after the deploy has changed the servers.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import statistics
import sys
import time

SCRIPTS = Path(__file__).resolve().parent
NOT_STARTED = 3


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proposal = load('proposal', 'proposal.py')
isuscope_sql = proposal.isuscope_sql


def results_dir(root):
    return Path(root) / '.local/bench-results'


def deliver(root, name, state, message, text):
    """The file the progress line names, printed too (it is the end of the benchmark's own output)."""
    directory = results_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{name}.txt').write_text(text.rstrip() + '\n')
    print(text.rstrip())
    proposal.progress_emit(root, 'benchmark', state, f'{name} {message}')


def tail(path, lines):
    try:
        return '\n'.join(Path(path).read_text(errors='replace').splitlines()[-lines:])
    except (OSError, TypeError):
        return ''


def check(root):
    try:
        pending = isuscope_sql.rows(root, "SELECT id FROM runs WHERE passed = 1 AND analysis_status = 'pending' "
                                          "ORDER BY started_at")
    except isuscope_sql.Unavailable:
        return 0  # isuscope run checks it again
    if not pending:
        return 0
    short = proposal.short_id(pending[0]['id'])
    name = time.strftime('not-started-%Y%m%dT%H%M%S')
    deliver(root, name, 'not-started', f'run {short} awaits its analysis', '\n'.join([
        f'# ベンチは始まらなかった（deployもしていない）: run {short}の採否がまだ記録されていない',
        f'- `python3 scripts/proposal.py next {short} --verdict <判定> --note "<決め手>" '
        '[--decide <測った投稿>=<採否>] [--merge <次の投稿>]`で記録してから、起動し直す。',
        '- この起動で測定中にした投稿は、次の起動でhookが測り直しへ戻す。']))
    return NOT_STARTED


def not_started(root, reason, output=None):
    name = time.strftime('not-started-%Y%m%dT%H%M%S')
    deliver(root, name, 'not-started', reason, '\n'.join([
        f'# ベンチは始まらなかった: {reason}',
        '- 原因を下の出力で確かめて直し、起動し直す。測定中にした投稿は、次の起動でhookが測り直しへ戻す。',
        '', tail(output, 25)]))


def same_config_spread(root, run, base):
    """The PASS scores of the base run's configuration up to this run, grouped as isuscope's brief groups them:
    a run measured again with `--repeat` joins the run it repeats, otherwise runs share a config_key."""
    started = str(run['started_at']).replace("'", '')
    rows = isuscope_sql.rows(root, f"""
        SELECT id, config_key, CASE WHEN repeat_reason IS NOT NULL THEN repeat_base_run_id END AS repeats, score
        FROM runs WHERE passed = 1 AND state = 'complete' AND score IS NOT NULL AND started_at <= '{started}'
        ORDER BY started_at, id""")
    group, by_key, by_id = [], {}, {}
    for position, row in enumerate(rows):
        same = by_id.get(row['repeats']) if row['repeats'] else None
        same = group[same] if same is not None else (group[by_key[row['config_key']]]
                                                    if row['config_key'] in by_key else position)
        group.append(same)
        by_id[row['id']] = position
        if row['config_key']:
            by_key[row['config_key']] = position
    if base not in by_id:
        return []
    first = group[by_id[base]]
    return [row['score'] for position, row in enumerate(rows) if group[position] == first]


def prebuild_of(root, branch):
    """The last prebuild that built the branch: each tree it made with the branch, and on which main commit."""
    try:
        record = json.loads((Path(root) / '.local/prebuild.json').read_text())
    except (OSError, ValueError):
        return None
    for entry in [record, *record.get('history', [])]:
        built = [result for result in entry.get('results', []) if result.get('proposal') == branch]
        if built:
            return (', '.join(f"{result['variant']} {result['status']}" for result in built)
                    + f"（{(entry.get('head') or '')[:8]}の上）")
    return None


def facts(root, run, output=None):
    run_id, short = run['id'], proposal.short_id(run['id'])
    passed = run['passed'] == 1
    base = proposal.main_session.main_branch(root)
    items = [proposal.refresh(root, data, base) for data in proposal.load_all(root)]
    lines = [f'# run {short} {"PASS" if passed else "FAIL"} {run["score"] if run["score"] is not None else "-"}'
             '（ここは事実だけ。採否はmainが決める）']
    measured = proposal.measured_in(root, items, run['commit_hash'], run_id) if run['commit_hash'] else []
    if measured:
        lines.append('- 測った投稿: ' + ' / '.join(
            f'{data["branch"]}（期待: {" ".join((data.get("expect") or "-").split())[:160]}）' for data, _, _ in measured))
    else:
        lines.append(f'- 測った投稿: なし（仮説: {" ".join((run.get("hypothesis") or "-").split())[:120]}）')
    base_run = proposal.base_of(root, run_id)
    if passed and base_run:
        scores = same_config_spread(root, run, base_run)
        if len(scores) >= 2:
            middle = statistics.median(scores)
            width = (max(scores) - min(scores)) / 2 / middle * 100 if middle else 0
            lines.append(f'- 振れ幅: 比較元{proposal.short_id(base_run)}と同じ構成のPASS {len(scores)}回 '
                         f'{min(scores):,}–{max(scores):,}（中央値{middle:,.0f}、±{width:.1f}%）')
        else:
            lines.append(f'- 振れ幅: 比較元{proposal.short_id(base_run)}の構成は1回しか測っていない（briefのspreadは別の構成のことがある）')
    elif passed:
        lines.append('- 振れ幅: 比較元が無い（`isuscope base`で決めていない）')
    waiting = sorted((data for data in items if data.get('status') in ('submitted', 'delivered')),
                     key=lambda data: data.get('first_submitted_at') or '')
    merged = [data['branch'] for data in items if data.get('status') == 'merged'
              and data['branch'] not in {d['branch'] for d, _, _ in measured}]
    others = {status: sum(1 for data in items if data.get('status') == status) for status in ('needs-sync', 'held')}
    if waiting or merged:
        shown = [f'{data["branch"]}（先build: {prebuild_of(root, data["branch"]) or "未"}）' for data in waiting[:3]]
        lines.append('- 次の候補: ' + ' / '.join(
            ([f'取り込み済み {", ".join(merged)}'] if merged else []) + shown
            + ([f'ほか{len(waiting) - 3}件'] if len(waiting) > 3 else [])))
    else:
        lines.append('- 次の候補: 待っている投稿は無い')
    if others['needs-sync'] or others['held']:
        lines.append(f'- 衝突 {others["needs-sync"]}件、保留 {others["held"]}件')
    if owed := proposal.analyses_owed(root, but=run_id):
        lines.append('- 詳しい分析がまだのrun: ' + ', '.join(proposal.short_id(r) for r in owed))
    decide = ' '.join(f'--decide {data["branch"]}=<採否>' for data, _, done in measured if not done)
    if passed:
        lines.append(f'- 次の手: `python3 scripts/proposal.py next {short} --verdict <判定> --note "<決め手>"'
                     f'{" " + decide if decide else ""} [--merge <次の投稿>]`、続けて`make bench`。'
                     '決めきれなければ、先にbrief・`isuscope query`を読む。')
    else:
        lines.append(f'- FAILしたので分析は要らない。下の出力で原因を確かめ、`python3 scripts/proposal.py next {short} '
                     '--requeue`（ベンチの外の原因。測り直す）か`--decide <投稿>=rejected --note "<原因>"`（投稿が原因）で投稿を扱う。')
        if output:
            lines += ['', '## isuscope runの出力（最後）', tail(output, 15)]
    return lines


def report(root, run, brief=None, output=None):
    try:
        found = proposal.find_run(root, run)
    except (proposal.Refused, isuscope_sql.Unavailable) as error:
        # The run exists (isuscope run printed it); only the index could not be read.
        found, lines = None, [f'# run {run}（isuscopeの記録を読めなかった: {error}）']
    if found:
        try:
            lines = facts(root, found, output)
        except isuscope_sql.Unavailable as error:
            lines = [f'# run {run}（isuscopeの記録を読めなかった: {error}）']
    passed = bool(found) and found['passed'] == 1
    text = '\n'.join(lines + ['', '## brief', tail(brief, 200) or '（briefを作れなかった）'])
    short = proposal.short_id(found['id']) if found else run
    score = found['score'] if found else '-'
    deliver(root, short, 'finished' if passed or not found else 'failed',
            f'{"PASS" if passed else "FAIL" if found else "?"} score {score}', text)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('check')
    p = sub.add_parser('report')
    p.add_argument('run')
    p.add_argument('--brief')
    p.add_argument('--output')
    p = sub.add_parser('not-started')
    p.add_argument('reason')
    p.add_argument('--output')
    args = parser.parse_args()
    root = proposal.main_session.root_repo()
    if args.action == 'check':
        return check(root)
    if args.action == 'report':
        if not re.fullmatch(r'[0-9a-f-]{4,36}', args.run):
            parser.error(f'not a run ID: {args.run}')
        report(root, args.run, args.brief, args.output)
    else:
        not_started(root, args.reason, args.output)
    return 0


if __name__ == '__main__':
    sys.exit(main())
