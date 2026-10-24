#!/usr/bin/env python3
"""The per-API waits main tunes while nothing else waits to be measured.

  waits.py show                                   the table, where it lives, and the change IDs to record
  waits.py set METHOD PATH --after MS|--before MS   (MS 0 drops that rule; --off drops both)
  waits.py history                                what was tried, from isuscope's runs and changes

The app reads ISUCON_WAITS once at start (config/waits/waits.go, wired in by lane `waits`), so the
table lives in the EnvironmentFile its unit reads: the same file deploy sends, the servers keep across
a reboot, and the organizers' re-test starts the app with. This script edits that line in the repo; it
commits that file alone, and `make deploy` (no build) puts it on the servers. scripts/benchmark-identity.py reports the
table as `identity.waits`, so every run records the waits it ran with, and a change shows as
`changed identity.waits`. Record each try as an isuscope change `wait-<method>-<path>-<phase>-<ms>`
on a run tagged `wait` and `wait:<METHOD PATH>`; `history` reads both back.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('isuscope_sql', Path(__file__).with_name('isuscope_sql.py'))
isuscope_sql = importlib.util.module_from_spec(spec)
spec.loader.exec_module(isuscope_sql)
VARIABLE = 'ISUCON_WAITS'
LINE = re.compile(r'^\s*(?:export\s+)?' + VARIABLE + r'=(.*)$')
COMMENT = '# APIごとの待ち（scripts/waits.py setで変える。形はconfig/waits/waits.go。空なら待たない）'


def git(*args, cwd=None, check=True):
    return subprocess.run(['git', *args], cwd=cwd or ROOT, capture_output=True, text=True, check=check).stdout


def root_repo():
    """Runs live in the root repository even when a worktree calls this script."""
    try:
        return Path(git('rev-parse', '--path-format=absolute', '--git-common-dir').strip()).parent
    except (OSError, subprocess.CalledProcessError):
        return ROOT


# --- the table -------------------------------------------------------------------------------

def is_placeholder(segment):
    return segment == '*' or segment.startswith(':') or (segment.startswith('{') and segment.endswith('}'))


def path_key(path):
    """Paths that match the same requests compare equal: every placeholder segment is `*`."""
    return '/'.join('*' if is_placeholder(segment) else segment for segment in path.split('/'))


def parse_rule(text):
    words = text.split()
    if len(words) != 4:
        raise ValueError(f'{text}: METHOD PATH before|after MS の4語で書く')
    method, path, phase, ms = words[0].upper(), words[1], words[2], words[3]
    if not re.fullmatch(r'[A-Z]+', method):
        raise ValueError(f'{text}: METHODが読めない')
    if not path.startswith('/'):
        raise ValueError(f'{text}: PATHは/で始める')
    if phase not in ('before', 'after'):
        raise ValueError(f'{text}: before・afterのどちらか')
    if not ms.isdigit():
        raise ValueError(f'{text}: MSは0以上の整数')
    return {'method': method, 'path': path, 'phase': phase, 'ms': int(ms)}


def parse(spec):
    return [parse_rule(text.strip()) for text in spec.split(';') if text.strip()]


def describe(rule):
    return f'{rule["method"]} {rule["path"]} {rule["phase"]} {rule["ms"]}'


def spec_of(rules):
    return ';'.join(describe(rule) for rule in rules)


def unquote(value):
    value = value.strip()
    try:
        words = shlex.split(value)
    except ValueError:
        return value
    return ' '.join(words) if words else ''


def change_id(rule):
    slug = re.sub(r'[^a-z0-9]+', '-', f'{rule["method"]} {rule["path"]}'.lower()).strip('-')
    return f'wait-{slug}-{rule["phase"]}-{rule["ms"]}'[:100]


# --- where the table lives -------------------------------------------------------------------

def tracked_with_line(root):
    listed = git('grep', '-l', '-e', f'^{VARIABLE}=', '-e', f'^export {VARIABLE}=', '--', 'config', cwd=root, check=False)
    return [root / line for line in listed.splitlines() if line]


def environment_files(root):
    """The EnvironmentFile each deployed unit reads, mapped back to the file deploy sends."""
    try:
        items = json.loads((root / 'config/sync.json').read_text()).get('items', [])
    except (OSError, ValueError):
        return []
    by_remote = {item.get('remote'): item.get('local') for item in items if item.get('type', 'file') == 'file'}
    found = []
    for item in items:
        local = item.get('local') or ''
        if not local.endswith('.service'):
            continue
        try:
            unit = (root / local).read_text()
        except OSError:
            continue
        for match in re.finditer(r'^EnvironmentFile=-?(\S+)', unit, re.M):
            local_env = by_remote.get(match.group(1))
            if local_env and (root / local_env) not in found:
                found.append(root / local_env)
    return found


def locate(root, chosen=None):
    if chosen:
        return Path(chosen) if Path(chosen).is_absolute() else root / chosen
    tracked = tracked_with_line(root)
    if len(tracked) == 1:
        return tracked[0]
    if len(tracked) > 1:
        raise SystemExit(f'{VARIABLE}が複数のfileにある（--fileで選ぶ）: ' + ', '.join(str(p.relative_to(root)) for p in tracked))
    candidates = environment_files(root)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SystemExit(f'{VARIABLE}を置くfileが分からない。アプリのunitが読むEnvironmentFileをsync.jsonで配っているか確かめ、'
                         '--file <repoのpath>で指定する')
    raise SystemExit('アプリのunitが読むEnvironmentFileが複数ある（--fileで選ぶ）: '
                     + ', '.join(str(p.relative_to(root)) for p in candidates))


def read_rules(path):
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    for line in lines:
        match = LINE.match(line)
        if match:
            return parse(unquote(match.group(1)))
    return []


def write_rules(path, rules):
    value = f'{VARIABLE}="{spec_of(rules)}"'
    lines = path.read_text().splitlines() if path.exists() else []
    for index, line in enumerate(lines):
        if LINE.match(line):
            lines[index] = value
            break
    else:
        lines += [COMMENT, value]
    path.write_text('\n'.join(lines) + '\n')


# --- commands --------------------------------------------------------------------------------

def show(root, chosen):
    path = locate(root, chosen)
    rules = read_rules(path)
    print(f'{path.relative_to(root)}: {VARIABLE}="{spec_of(rules)}"')
    for rule in rules:
        print(f'  {describe(rule)}  （記録: --change {change_id(rule)}）')
    if not rules:
        print('  待ちは無い')


def set_rule(root, args):
    path = locate(root, args.file)
    rules = read_rules(path)
    before = spec_of(rules)
    key = (args.method.upper(), path_key(args.path))
    if args.off:
        rules = [rule for rule in rules if (rule['method'], path_key(rule['path'])) != key]
        changed = None
    else:
        phase, ms = ('after', args.after) if args.after is not None else ('before', args.before)
        changed = parse_rule(f'{args.method} {args.path} {phase} {ms}')
        rules = [rule for rule in rules if (rule['method'], path_key(rule['path']), rule['phase']) != key + (phase,)]
        if ms:
            rules.append(changed)
    write_rules(path, rules)
    after = spec_of(rules)
    print(f'{path.relative_to(root)}: {VARIABLE}')
    print(f'  前: "{before}"')
    print(f'  後: "{after}"')
    route = f'{args.method.upper()} {args.path}'
    record = change_id(changed) if changed and changed['ms'] else f'wait-{re.sub(r"[^a-z0-9]+", "-", route.lower()).strip("-")}-off'
    phase = 'before' if args.before is not None else 'after'
    what = 'off' if args.off else f'{phase} {phase_ms(before, args, phase)}→{phase_ms(after, args, phase)}ms'
    base = latest_run(root_repo())
    message = f'Wait {route}: {describe(changed) if changed and changed["ms"] else "off"}'
    # Only the table's file: other changes in the worktree are not part of this try.
    committed = subprocess.run(['git', 'commit', '-q', '-m', message, '--', str(path)], cwd=root,
                               capture_output=True, text=True)
    print(f'commit: {message}' if committed.returncode == 0
          else f'commitできなかった（{(committed.stderr or committed.stdout).strip()[:200]}）。`git commit -m "{message}" -- {path.relative_to(root)}`')
    print('次:')
    # bench.sh deploys (no build for the table alone) and ends like any benchmark: the facts reach main's Monitor.
    print(f'  scripts/bench.sh{" --base " + base if base else ""} --tag wait --tag "wait:{route}" --hypothesis "wait: {route} {what}。'
          f'基準 {base or "<短縮ID>"}。期待: "（run_in_background。期待だけを書き足す）')
    print(f'  python3 scripts/proposal.py next RUN --verdict supported|rejected --note "<決め手>" '
          f'--decide {record[:100]}=accepted|rejected（不採用なら`git revert`で前の値へ戻す）')


def phase_ms(spec, args, phase):
    """The ms the route waits in that phase, in a table spec (0 when it has no rule there)."""
    key = (args.method.upper(), path_key(args.path), phase)
    for rule in parse(spec):
        if (rule['method'], path_key(rule['path']), rule['phase']) == key:
            return rule['ms']
    return 0


def latest_run(root):
    """The last finished run: the configuration a wait is tried against."""
    try:
        runs = isuscope_sql.rows(root, "SELECT id FROM runs WHERE passed = 1 AND state IN ('complete', 'degraded') "
                                       "AND score IS NOT NULL ORDER BY started_at DESC, id DESC LIMIT 1")
    except isuscope_sql.Unavailable:
        return None  # the next steps still print, with a placeholder for the base run
    return runs[0]['id'][-8:] if runs else None


def load_runs(root):
    """Finished runs with a score, oldest first, with their tags and the waits they ran with."""
    rows = isuscope_sql.rows(root, """
        SELECT runs.id, runs.started_at, runs.score, runs.passed = 1 AS passed, runs.hypothesis,
               COALESCE((SELECT value FROM run_identity WHERE run_id = runs.id AND key = 'waits'), '') AS waits,
               COALESCE((SELECT group_concat(tag, char(10)) FROM run_tags WHERE run_id = runs.id), '') AS tags
        FROM runs WHERE runs.state IN ('complete', 'degraded') AND runs.score IS NOT NULL
        ORDER BY runs.started_at, runs.id""")
    return [{**row, 'short': row['id'][-8:], 'tags': row['tags'].split('\n') if row['tags'] else []} for row in rows]


def load_changes(root):
    """The wait-* changes, oldest first, each with its decisions (oldest first) and their runs."""
    changes = isuscope_sql.rows(root, "SELECT id, created_at FROM changes WHERE id LIKE 'wait-%' ORDER BY created_at, id")
    decisions = isuscope_sql.rows(root, """
        SELECT d.change_id, d.created_at, d.status, d.reason,
               COALESCE((SELECT group_concat(run_id, char(10)) FROM change_decision_runs WHERE decision_id = d.id), '')
                 AS runs
        FROM change_decisions d WHERE d.change_id LIKE 'wait-%' ORDER BY d.created_at, d.id""")
    by_change = {}
    for decision in decisions:
        decision['runs'] = decision['runs'].split('\n') if decision['runs'] else []
        by_change.setdefault(decision['change_id'], []).append(decision)
    return [(change, by_change.get(change['id'], [])) for change in changes]


def history(root):
    runs = load_runs(root)
    previous = None
    print('waitを変えたrun（tag wait、または前のrunとidentity.waitsが違うrun）:')
    shown = 0
    for run in runs:
        if 'wait' in run['tags'] or (previous is not None and run['waits'] != previous):
            mark = 'PASS' if run['passed'] else 'FAIL'
            print(f'  {run["started_at"][:16]} {run["short"]} {mark} {run["score"]:>9}  waits="{run["waits"]}"')
            shown += 1
        previous = run['waits']
    if not shown:
        print('  まだ無い')
    print('waitの変更と採否（isuscope change、IDがwait-で始まるもの）:')
    changes = load_changes(root)
    for change, decisions in changes:
        latest = decisions[-1] if decisions else {}
        evidence = ', '.join(run[-8:] for d in decisions for run in d['runs'])
        print(f'  {change["id"]}: {latest.get("status", "未判定")}（run {evidence or "-"}）')
        if latest.get('reason'):
            print(f'    {latest["reason"][:200]}')
    if not changes:
        print('  まだ無い')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    show_parser = sub.add_parser('show')
    show_parser.add_argument('--file')
    set_parser = sub.add_parser('set')
    set_parser.add_argument('method')
    set_parser.add_argument('path')
    group = set_parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--after', type=int, metavar='MS')
    group.add_argument('--before', type=int, metavar='MS')
    group.add_argument('--off', action='store_true')
    set_parser.add_argument('--file', help='ISUCON_WAITSを置くfile（repoのpath）。無ければ探す')
    sub.add_parser('history')
    args = parser.parse_args()
    root = ROOT
    try:
        if args.action == 'show':
            show(root, args.file)
        elif args.action == 'set':
            set_rule(root, args)
        else:
            history(root_repo())
    except (ValueError, isuscope_sql.Unavailable) as error:
        raise SystemExit(str(error))
    return 0


if __name__ == '__main__':
    sys.exit(main())
