#!/usr/bin/env python3
"""Proposals: a worktree hands main a branch to measure, and main hands back its decision.

  proposal.py submit --hypothesis TEXT --expect TEXT --verified TEXT [--based-on RUN] [--note TEXT]
  proposal.py list [--all]                 the proposals and their state
  proposal.py show [BRANCH]                one proposal (default: this worktree's branch)
  proposal.py pending                      main: the proposals not handed to it yet (as the hook does)
  proposal.py decide BRANCH accepted|rejected|deferred [--run ID] [--note TEXT]
  proposal.py withdraw [BRANCH]

A person or a Claude Code session working in a worktree (make worktree) commits its change and
submits it from that worktree. The proposal is .local/proposals/<branch>.json in the root
repository: the branch and commit, the hypothesis, the observation it expects, what was checked
locally, and the files it changes. Submitting again after more commits replaces it.

When main starts a benchmark in the background, scripts/bench-hook.py --post hands it the
proposals it has not seen at their current commit that merge into main's branch without a
conflict (git merge-tree), so main reads them and builds the next candidate while the benchmark
runs. One that conflicts is not handed over: it is marked needs-sync, for its author to merge
main's branch and submit again. A proposal whose commit main's branch already holds is merged.
main records its decision with `decide`, which the author reads with `show` (and the Claude Code
mod draws above the author's prompt).

Submitting and deciding each write a line to .local/progress.log (`proposal submitted`, `proposal
accepted|rejected|deferred`), which main and the improve session (/improve) follow with Monitor:
main, with no benchmark running, picks a new proposal up with `pending` instead of waiting for the
next benchmark, and improve learns the decision without polling.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

SCRIPTS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('main_session', SCRIPTS / 'main-session.py')
main_session = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(main_session)


def progress(state, message):
    """A line in .local/progress.log; never fails the proposal (progress.py may be absent)."""
    try:
        spec = importlib.util.spec_from_file_location('progress', SCRIPTS / 'progress.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.emit('proposal', state, message, root=SCRIPTS.parent)
    except (OSError, ImportError, AttributeError):
        pass

OPEN = ('submitted', 'delivered', 'needs-sync')
DECISIONS = ('accepted', 'rejected', 'deferred')
HINT = ('worktreeからの投稿です（mainのbranchへ衝突なく入ることを確かめ済み）。ベンチを待つ間に読み、'
        '次に測るものを選んだら`git merge --no-ff <branch>`して`make build`しておく。'
        '互いに独立した投稿は1回のベンチでまとめて測ってよい（仮説に投稿ごとの期待を並べ、'
        '`isuscope analyze`を投稿ごとに`--change <branch>`で記録する）。採否は`python3 scripts/proposal.py decide <branch> '
        'accepted|rejected|deferred --run <短縮ID> --note "理由"`で投稿者へ返す（isuscopeの`analyze --change`とは別に記録する）。')


def git(*args, cwd=None):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)


def directory(root):
    return Path(root) / '.local/proposals'


def file_of(root, branch):
    return directory(root) / (branch.replace('/', '__') + '.json')


def load_all(root):
    items = []
    for item in sorted(directory(root).glob('*.json')) if directory(root).is_dir() else []:
        try:
            data = json.loads(item.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get('branch'):
            items.append(data)
    return items


def load(root, branch):
    try:
        return json.loads(file_of(root, branch).read_text())
    except (OSError, ValueError):
        return None


def save(root, data):
    target = file_of(root, data['branch'])
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(target)


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def current_branch(cwd=None):
    return git('symbolic-ref', '--quiet', '--short', 'HEAD', cwd=cwd).stdout.strip()


def submit(root, hypothesis, expect, verified, note=None, cwd=None, based_on=None):
    here = main_session.toplevel(cwd)
    if here is None or here == Path(root).resolve():
        raise SystemExit('投稿はworktree（make worktree BRANCH=...）から行う。repoのrootはmainの作業場所。')
    branch = current_branch(cwd)
    base = main_session.main_branch(root)
    if not branch or branch == base:
        raise SystemExit(f'mainのbranch（{base}）ではなく、自分のbranchから投稿する。')
    # The conversation log keeps being written while the session runs; it is not part of the change.
    dirty = [line for line in git('status', '--porcelain', cwd=cwd).stdout.splitlines()
             if line and not line[3:].startswith('docs/agent-history/')]
    if dirty:
        raise SystemExit('commitしていない変更がある。commitしてから投稿する:\n' + '\n'.join(dirty[:10]))
    ahead = git('rev-list', '--count', f'{base}..HEAD', cwd=cwd).stdout.strip()
    if ahead in ('', '0'):
        raise SystemExit(f'{base}に無いcommitが無い。変更をcommitしてから投稿する。')
    commit = git('rev-parse', 'HEAD', cwd=cwd).stdout.strip()
    files = git('diff', '--name-only', f'{base}...HEAD', cwd=cwd).stdout.split()
    agent, session = main_session.current_session()
    previous = load(root, branch) or {}
    data = {'branch': branch, 'commit': commit, 'base': base, 'worktree': str(here),
            'hypothesis': hypothesis, 'expect': expect, 'verified': verified, 'note': note or '',
            'based_on': based_on or '',
            'files': files, 'author': {'agent': agent, 'session': session},
            'submitted_at': now(), 'first_submitted_at': previous.get('first_submitted_at', now()),
            'status': 'submitted', 'delivered': previous.get('delivered', {}), 'history': previous.get('history', [])}
    if previous.get('commit') and previous.get('commit') != commit:
        data['history'].append({'commit': previous['commit'], 'status': previous.get('status'),
                                'decision': previous.get('decision')})
    save(root, data)
    progress('submitted', f'{branch} {commit[:8]}: {hypothesis[:120]}')
    return f'{branch}（{commit[:8]}）を投稿した。mainが次にベンチを起動したときに渡る。状態は`python3 scripts/proposal.py show`。'


def merge_check(root, base, commit):
    """None when it merges cleanly, else the conflicting files ([] when git cannot tell)."""
    result = git('merge-tree', '--write-tree', '--name-only', '--no-messages', base, commit, cwd=root)
    if result.returncode == 0:
        return None
    if result.returncode == 1:
        return [line for line in result.stdout.splitlines()[1:] if line.strip()]
    return []


def refresh(root, data, base=None):
    """Mark a proposal merged once main's branch holds its commit, or needs-sync when it conflicts."""
    base = base or main_session.main_branch(root)
    if data.get('status') not in OPEN:
        return data
    if git('merge-base', '--is-ancestor', data['commit'], base, cwd=root).returncode == 0:
        data.update(status='merged', merged_at=now())
        save(root, data)
        return data
    conflicts = merge_check(root, base, data['commit'])
    if conflicts is not None:
        if data.get('status') != 'needs-sync':
            data.update(status='needs-sync', conflicts=conflicts, checked_at=now())
            save(root, data)
    elif data.get('status') == 'needs-sync':
        data.update(status='submitted', conflicts=[])
        save(root, data)
    return data


def age(stamp, at=None):
    try:
        then = time.mktime(time.strptime(stamp, '%Y-%m-%dT%H:%M:%S%z'))
    except (TypeError, ValueError):
        return '?'
    return f'{max(0, int(((at or time.time()) - then) // 60))}分前'


def deliver(root, session, at=None):
    """The proposals this main session has not been handed at their current commit, as hook context."""
    base = main_session.main_branch(root)
    fresh, held = [], []
    for data in load_all(root):
        data = refresh(root, data, base)
        if data.get('status') == 'needs-sync':
            # Told once per commit, so main knows a proposal waits on its author rather than on it.
            if data.get('held_reported', {}).get(session) != data['commit']:
                held.append(data['branch'])
                data.setdefault('held_reported', {})[session] = data['commit']
                save(root, data)
            continue
        if data.get('status') not in ('submitted', 'delivered'):
            continue
        if data.get('delivered', {}).get(session) == data['commit']:
            continue
        fresh.append(data)
    if not fresh and not held:
        return None
    parts = [HINT] if fresh else []
    for data in fresh:
        files = data.get('files') or []
        shown = ', '.join(files[:8]) + (f' ほか{len(files) - 8}件' if len(files) > 8 else '')
        author = data.get('author') or {}
        parts.append('\n'.join([
            f'### {data["branch"]}（{data["commit"][:8]}、投稿 {age(data.get("submitted_at"), at)}、{author.get("agent") or "?"}）',
            f'- 仮説: {data.get("hypothesis")}',
            f'- 期待する観測: {data.get("expect")}',
            f'- 確かめたこと: {data.get("verified")}',
            *([f'- 根拠のrun: {data["based_on"]}'] if data.get('based_on') else []),
            *([f'- 補足: {data["note"]}'] if data.get('note') else []),
            f'- 変えたfile: {shown or "(なし)"}',
            f'- 中身: `git log --stat {base}..{data["branch"]}`、取り込み: `git merge --no-ff {data["branch"]}`',
        ]))
        data.setdefault('delivered', {})[session] = data['commit']
        data['status'] = 'delivered'
        data['delivered_at'] = now()
        save(root, data)
    if held:
        parts.append(f'mainのbranchと衝突するので渡していない投稿: {", ".join(held)}（投稿者が追従して投稿し直す）')
    return '\n\n'.join(parts)


def decide(root, branch, decision, run=None, note=None):
    data = load(root, branch)
    if not data:
        raise SystemExit(f'{branch}の投稿は無い。`python3 scripts/proposal.py list --all`で確かめる。')
    agent, session = main_session.current_session()
    data.update(status=decision, decision={'decision': decision, 'run': run or '', 'note': note or '',
                                           'at': now(), 'by': {'agent': agent, 'session': session},
                                           'commit': data['commit']})
    save(root, data)
    progress(decision, f'{branch} run {run or "-"}: {note or ""}')
    return f'{branch}を{decision}にした。'


def withdraw(root, branch):
    data = load(root, branch)
    if not data:
        raise SystemExit(f'{branch}の投稿は無い。')
    data.update(status='withdrawn', withdrawn_at=now())
    save(root, data)
    return f'{branch}の投稿を取り下げた。'


def describe(data):
    lines = [f'{data["branch"]}  {data.get("status")}  {data["commit"][:8]}  投稿 {age(data.get("submitted_at"))}',
             f'  仮説: {data.get("hypothesis")}', f'  期待する観測: {data.get("expect")}',
             f'  確かめたこと: {data.get("verified")}']
    if data.get('based_on'):
        lines.append(f'  根拠のrun: {data["based_on"]}')
    if data.get('status') == 'needs-sync':
        lines.append(f'  mainのbranchと衝突: {", ".join(data.get("conflicts") or []) or "(詳細不明)"}。'
                     f'`git merge {data.get("base") or "main"}`で追従してcommitし、submitし直す。')
    if data.get('decision'):
        decision = data['decision']
        lines.append(f'  mainの判断: {decision.get("decision")}  run {decision.get("run") or "-"}  {decision.get("note") or ""}')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('submit')
    p.add_argument('--hypothesis', required=True, help='何を変えて、なぜ速くなる（点が上がる）と考えるか')
    p.add_argument('--expect', required=True, help='期待する観測値（route、SQL、CPUなど、briefとqueryで確かめられるもの）')
    p.add_argument('--verified', required=True, help='ローカルで確かめたこと（build、test、local-check、local-flowなど）')
    p.add_argument('--based-on', help='根拠にしたrunの短縮ID（mainが前提の古さを確かめる）')
    p.add_argument('--note')
    p = sub.add_parser('list')
    p.add_argument('--all', action='store_true')
    p = sub.add_parser('show')
    p.add_argument('branch', nargs='?')
    sub.add_parser('pending')
    p = sub.add_parser('decide')
    p.add_argument('branch')
    p.add_argument('decision', choices=DECISIONS)
    p.add_argument('--run')
    p.add_argument('--note')
    p = sub.add_parser('withdraw')
    p.add_argument('branch', nargs='?')
    args = parser.parse_args()
    root = main_session.root_repo()
    if args.action == 'submit':
        print(submit(root, args.hypothesis, args.expect, args.verified, args.note, based_on=args.based_on))
    elif args.action == 'pending':
        _, session = main_session.current_session()
        if main_session.is_main(root, session) is False:
            raise SystemExit('投稿を受け取るのはmain（/mainで宣言したセッション）。')
        print(deliver(root, session or 'unknown') or '新しい投稿は無い。')
    elif args.action == 'decide':
        print(decide(root, args.branch, args.decision, args.run, args.note))
    elif args.action == 'withdraw':
        print(withdraw(root, args.branch or current_branch()))
    elif args.action == 'show':
        branch = args.branch or current_branch()
        data = load(root, branch)
        if not data:
            raise SystemExit(f'{branch}の投稿は無い。')
        print(describe(refresh(root, data)))
    else:
        items = [refresh(root, data) for data in load_all(root)]
        items = [data for data in items if args.all or data.get('status') in OPEN]
        print('\n'.join(describe(data) for data in items) if items else '投稿は無い。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
