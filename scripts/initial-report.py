#!/usr/bin/env python3
"""Measure the initial work of a practice from what is already recorded.

The four numbers that show whether the changes to the initial work worked:
- minutes from the start to the baseline (the first passing survey-run)
- runs in the window that failed on a connection or file limit (brief's `limits`)
- seconds isuscope spent after each benchmark (collection and parsing)
- questions the agents stopped on, and how long they waited for the answer

Sources: isuscope (`isuscope sql`, `isuscope brief`, isuscope-data/runs/*/run.json) and the
conversation events in docs/agent-history. Nothing is changed. Run it from the practice repository:

    python3 scripts/initial-report.py --start 2026-10-10T15:01:05+09:00
"""
import argparse
import glob
import json
import statistics
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

QUESTION_MARKERS = ('？', '?', 'よければ', 'どちら', 'しますか', 'いいですか', 'よいですか', 'ください')
FINISHED_STATES = ('complete', 'degraded')


def when(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def isuscope(*args):
    completed = subprocess.run(['isuscope', *args], capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(f"isuscope {' '.join(args)} failed: {completed.stderr.strip()}")
    return json.loads(completed.stdout)


def table(section):
    """isuscope prints tables as columns and rows; turn them back into dicts."""
    if not isinstance(section, dict):
        return []
    columns = section.get('columns') or []
    return [dict(zip(columns, row)) for row in section.get('rows') or []]


def runs(root):
    rows = table(isuscope('sql', 'SELECT id, started_at, finished_at, mode, state, score, passed '
                                 'FROM runs ORDER BY started_at'))
    for row in rows:
        row['started'] = when(row['started_at'])
        row['finished'] = when(row['finished_at']) if row.get('finished_at') else None
        manifest = next(iter(glob.glob(str(root / 'isuscope-data/runs' / row['id'] / 'run.json'))), None)
        row['after_seconds'] = None
        if manifest:
            benchmark = json.loads(Path(manifest).read_text()).get('benchmark') or {}
            if benchmark.get('finished_at') and row['finished']:
                row['after_seconds'] = (row['finished'] - when(benchmark['finished_at'])).total_seconds()
    return rows


def limit_failures(failed):
    found = []
    for run in failed:
        try:
            limits = table(isuscope('brief', run['id'][-8:]).get('limits'))
        except (RuntimeError, ValueError):
            continue
        if limits:
            found.append((run['id'][-8:], sorted({row['setting'] for row in limits})))
    return found


def questions(root, start, end):
    """Agent turns ending in a question, and the time until the human's next message."""
    asked, waited = 0, 0.0
    for path in sorted(glob.glob(str(root / 'docs/agent-history/*.events.jsonl'))):
        last = None
        for line in Path(path).read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            at = when(event['received_at'])
            text = event.get('text') or ''
            if event.get('kind') == 'assistant':
                last = (at, text)
            elif event.get('kind') == 'user' and text and not text.startswith('<task-notification'):
                if last and start <= at <= end and any(m in last[1][-400:] for m in QUESTION_MARKERS):
                    gap = (at - last[0]).total_seconds()
                    if gap < 1800:
                        asked += 1
                        waited += gap
                last = None
    return asked, waited


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--start', required=True, help='start of the practice or contest, ISO 8601 with offset')
    parser.add_argument('--hours', type=float, default=2.0, help='length of the window after the start')
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    start = when(args.start)
    end = start + timedelta(hours=args.hours)
    all_runs = runs(args.root)
    window = [run for run in all_runs if start <= run['started'] <= end]
    baseline = next((run for run in all_runs if run['started'] >= start and run['mode'] == 'survey-run'
                     and run['state'] in FINISHED_STATES), None)
    failed_surveys = [run for run in all_runs if run['started'] >= start and run['mode'] == 'survey-run'
                      and baseline and run['started'] < baseline['started']]
    failed = [run for run in window if run['state'] in ('failed', 'aborted')]
    after = [run['after_seconds'] for run in window if run['after_seconds'] is not None]
    best_60 = None
    if baseline and baseline['finished']:
        limit = baseline['finished'] + timedelta(minutes=60)
        scores = [run['score'] for run in all_runs if run['passed'] == 1 and run['score'] is not None
                  and run['finished'] and baseline['started'] <= run['started'] and run['finished'] <= limit]
        best_60 = max(scores) if scores else None
    span = ((window[-1]['finished'] or window[-1]['started']) - window[0]['started']).total_seconds() if window else 0
    asked, waited = questions(args.root, start, end)
    report = {
        'start': start.isoformat(),
        'window_hours': args.hours,
        'baseline_minutes': round((baseline['finished'] - start).total_seconds() / 60, 1)
        if baseline and baseline['finished'] else None,
        'baseline_run': baseline['id'][-8:] if baseline else None,
        'survey_runs_before_baseline': len(failed_surveys),
        'runs': len(window),
        'passed': sum(1 for run in window if run['passed'] == 1),
        'failed': len(failed),
        'cycle_minutes': round(span / len(window) / 60, 2) if window else None,
        'limit_failures': [{'run': run, 'settings': settings} for run, settings in limit_failures(failed)],
        'after_seconds_median': round(statistics.median(after), 1) if after else None,
        'after_seconds_total_minutes': round(sum(after) / 60, 1) if after else None,
        'best_score_60_minutes_after_baseline': best_60,
        'questions': asked,
        'question_wait_minutes': round(waited / 60, 1),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    hours = f'{args.hours:g}'
    print(f'初動の指標（{start:%Y-%m-%d %H:%M:%S}から{hours}時間）')
    print(f"- 開始からbaselineまで: {report['baseline_minutes']}分（{report['baseline_run']}、"
          f"その前のsurvey-run {report['survey_runs_before_baseline']}回）")
    print(f"- run: {report['runs']}回（PASS {report['passed']}、FAIL・中断 {report['failed']}）、"
          f"1サイクル平均 {report['cycle_minutes']}分")
    settings = '、'.join(f"{item['run']} {' / '.join(item['settings'])}" for item in report['limit_failures'])
    print(f"- 上限に当たったFAIL: {len(report['limit_failures'])}回" + (f"（{settings}）" if settings else ''))
    print(f"- isuscopeの後処理: 中央値 {report['after_seconds_median']}秒、合計 {report['after_seconds_total_minutes']}分")
    print(f"- baseline後60分の最高スコア: {report['best_score_60_minutes_after_baseline']}")
    print(f"- 確認の質問: {report['questions']}回、返事待ちの合計 {report['question_wait_minutes']}分"
          '（並行するセッションが動いていた時間を含む）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
