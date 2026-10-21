#!/usr/bin/env python3
"""The per-API waits main tunes while nothing else waits to be measured.

  waits.py show                                   the table, where it lives, and the change IDs to record
  waits.py set METHOD PATH --after MS|--before MS   (MS 0 drops that rule; --off drops both)
  waits.py history                                what was tried, from isuscope's runs and changes

The app reads ISUCON_WAITS once at start (config/waits/waits.rs, wired in by lane `waits`), so the
table lives in the EnvironmentFile its unit reads: the same file deploy sends, the servers keep across
a reboot, and the organizers' re-test starts the app with. This script edits that line in the repo; a
commit and `make deploy` (no build) put it on the servers. scripts/benchmark-identity.py reports the
table as `identity.waits`, so every run records the waits it ran with, and a change shows as
`changed identity.waits`. Record each try as an isuscope change `wait-<method>-<path>-<phase>-<ms>`
on a run tagged `wait` and `wait:<METHOD PATH>`; `history` reads both back.
"""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
VARIABLE = 'ISUCON_WAITS'
LINE = re.compile(r'^\s*(?:export\s+)?' + VARIABLE + r'=(.*)$')
COMMENT = '# APIごとの待ち（scripts/waits.py setで変える。形はconfig/waits/waits.rs。空なら待たない）'


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
    print('次:')
    print(f'  git commit -am "Wait {route}: {describe(changed) if changed and changed["ms"] else "off"}" && make deploy')
    print(f'  isuscope run --tag wait --tag "wait:{route}" --hypothesis "wait: {route} {what}。'
          f'基準 {base or "<短縮ID>"}。期待: "（run_in_background。期待だけを書き足す）')
    print(f'  isuscope analyze RUN ... --change {record[:100]} --decision accepted|rejected')


def phase_ms(spec, args, phase):
    """The ms the route waits in that phase, in a table spec (0 when it has no rule there)."""
    key = (args.method.upper(), path_key(args.path), phase)
    for rule in parse(spec):
        if (rule['method'], path_key(rule['path']), rule['phase']) == key:
            return rule['ms']
    return 0


def latest_run(root):
    """The last finished run: the configuration a wait is tried against."""
    runs = [run for run in load_runs(root) if run['passed']]
    return runs[-1]['short'] if runs else None


def load_runs(root):
    runs = []
    for path in sorted((root / 'isuscope-data/runs').glob('*/run.json')):
        try:
            run = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        bench = run.get('benchmark') or {}
        if run.get('state') != 'complete' or bench.get('score') is None:
            continue
        values = (run.get('identity') or {}).get('values') or {}
        runs.append({'short': run['id'][-8:], 'started_at': run.get('started_at') or '', 'score': bench['score'],
                     'passed': bench.get('passed'), 'tags': run.get('tags') or [], 'waits': values.get('waits', ''),
                     'hypothesis': run.get('hypothesis') or ''})
    return sorted(runs, key=lambda run: run['started_at'])


def load_changes(root):
    changes = []
    for directory in sorted((root / 'isuscope-data/changes').glob('wait-*')):
        try:
            change = json.loads((directory / 'change.json').read_text())
        except (OSError, ValueError):
            continue
        decisions = []
        for path in sorted((directory / 'decisions').glob('*.json')):
            try:
                decisions.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                continue
        decisions.sort(key=lambda decision: decision.get('created_at', ''))
        changes.append((change, decisions))
    return sorted(changes, key=lambda item: item[0].get('created_at', ''))


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
        evidence = ', '.join(e.get('run_id', '')[-8:] for d in decisions for e in d.get('evidence', []))
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
    except ValueError as error:
        raise SystemExit(str(error))
    return 0


if __name__ == '__main__':
    sys.exit(main())
