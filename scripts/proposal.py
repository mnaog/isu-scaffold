#!/usr/bin/env python3
"""Proposals: a worktree hands main a branch to measure, and main hands back its decision.

  proposal.py submit --hypothesis TEXT --expect TEXT --verified TEXT [--note TEXT]
  proposal.py list [--all]                 the proposals and their state
  proposal.py show [BRANCH]                one proposal (default: this worktree's branch)
  proposal.py decide BRANCH accepted|rejected|deferred [--run ID] [--note TEXT] [--commit COMMIT]
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
mod draws above the author's prompt). A decision belongs to the commit main merged and measured:
when the author submitted again after main merged an earlier commit, the decision goes to that
earlier commit and the new one stays open. `accepted` needs the run that measured it. Until main
decides, a merged proposal stays in `list` and each benchmark reminds main of it, since merging
is not the end of a proposal.
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

OPEN = ('submitted', 'delivered', 'needs-sync')
DECISIONS = ('accepted', 'rejected', 'deferred')
HINT = ('worktreeからの投稿です（mainのbranchへ衝突なく入ることを確かめ済み）。ベンチを待つ間に読み、'
        '次に測るものを選んだら`git merge --no-ff <branch>`して`make build`しておく。'
        '採否は`python3 scripts/proposal.py decide <branch> accepted|rejected|deferred --run <短縮ID> --note "理由"`'
        'で投稿者へ返す（isuscopeの`analyze --change`とは別に記録する）。判断はmainへ取り込んで測ったcommitに付く。')


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


def submit(root, hypothesis, expect, verified, note=None, cwd=None):
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
            'files': files, 'author': {'agent': agent, 'session': session},
            'submitted_at': now(), 'first_submitted_at': previous.get('first_submitted_at', now()),
            'status': 'submitted', 'delivered': previous.get('delivered', {}), 'history': previous.get('history', [])}
    if previous.get('commit') and previous.get('commit') != commit:
        status = previous.get('status')
        # main may have merged it since anyone last looked; its decision is still owed.
        if status in OPEN and git('merge-base', '--is-ancestor', previous['commit'], base, cwd=root).returncode == 0:
            status = 'merged'
        data['history'].append({'commit': previous['commit'], 'status': status,
                                'decision': previous.get('decision')})
    save(root, data)
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


def open_entries(data):
    """The current commit and the earlier ones, newest first, down to the newest that main decided.

    A decision covers what came before it: main measured a later commit that held the older ones.
    """
    entries = []
    for entry in [data, *reversed(data.get('history') or [])]:
        if entry.get('decision'):
            break
        entries.append(entry)
    return entries


def undecided(data):
    """The commits of a proposal that main merged but has not decided, newest first."""
    return [entry['commit'] for entry in open_entries(data) if entry.get('status') == 'merged']


def deliver(root, session, at=None):
    """The proposals this main session has not been handed at their current commit, as hook context."""
    base = main_session.main_branch(root)
    fresh, held, waiting = [], [], []
    for data in load_all(root):
        data = refresh(root, data, base)
        # Every benchmark until main decides: a merged proposal is measured, not finished.
        waiting.extend(f'{data["branch"]}（{commit[:8]}）' for commit in undecided(data))
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
    if not fresh and not held and not waiting:
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
    if waiting:
        parts.append(f'取り込んだが採否を返していない投稿: {", ".join(waiting)}。測ったrunを付けて'
                     '`python3 scripts/proposal.py decide <branch> accepted|rejected|deferred --run <短縮ID>`で返す。')
    return '\n\n'.join(parts)


def measured_commit(root, data, base, commit=None):
    """The commit a decision belongs to: the one given, else what main merged and has not decided.

    The current commit when main's branch holds it. Otherwise the newest earlier commit main merged
    and left undecided: the author submitted again after main took it, and main's run measured that
    one. None when main merged none of them (a decision on the current commit without measuring it).
    """
    entries = [data, *reversed(data.get('history') or [])]
    if commit:
        found = [entry['commit'] for entry in entries if entry['commit'].startswith(commit)]
        if not found:
            raise SystemExit(f'{data["branch"]}の投稿に{commit}は無い。`python3 scripts/proposal.py show {data["branch"]}`で確かめる。')
        return found[0]
    held = lambda entry: git('merge-base', '--is-ancestor', entry['commit'], base, cwd=root).returncode == 0
    if held(data):
        return data['commit']
    return next((entry['commit'] for entry in open_entries(data)[1:] if held(entry)), None)


def decide(root, branch, decision, run=None, note=None, commit=None):
    data = load(root, branch)
    if not data:
        raise SystemExit(f'{branch}の投稿は無い。`python3 scripts/proposal.py list --all`で確かめる。')
    if decision == 'accepted' and not run:
        raise SystemExit('acceptedには、測ったrunを`--run <短縮ID>`で付ける。')
    base = main_session.main_branch(root)
    target = measured_commit(root, data, base, commit) or data['commit']
    if decision == 'accepted' and git('merge-base', '--is-ancestor', target, base, cwd=root).returncode != 0:
        raise SystemExit(f'{branch}の{target[:8]}は{base}に入っていない。acceptedは取り込んで測ったcommitにだけ付ける。')
    agent, session = main_session.current_session()
    record = {'decision': decision, 'run': run or '', 'note': note or '', 'at': now(),
              'by': {'agent': agent, 'session': session}, 'commit': target}
    entry = data if target == data['commit'] else next(e for e in data['history'] if e['commit'] == target)
    entry.update(status=decision, decision=record)
    save(root, data)
    if entry is data:
        return f'{branch}（{target[:8]}）を{decision}にした。'
    return (f'{branch}の{target[:8]}（{base}へ取り込んだcommit）を{decision}にした。'
            f'今の投稿{data["commit"][:8]}はその後に出たもので、判断は付けていない。')


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
    if data.get('status') == 'needs-sync':
        lines.append(f'  mainのbranchと衝突: {", ".join(data.get("conflicts") or []) or "(詳細不明)"}。'
                     f'`git merge {data.get("base") or "main"}`で追従してcommitし、submitし直す。')
    if data.get('decision'):
        decision = data['decision']
        lines.append(f'  mainの判断: {decision.get("decision")}  run {decision.get("run") or "-"}  {decision.get("note") or ""}')
    elif data.get('status') == 'merged':
        lines.append('  mainへ取り込み済み。採否はまだ返っていない。')
    for entry in reversed(data.get('history') or []):
        decision = entry.get('decision') or {}
        state = (f'{decision.get("decision")}  run {decision.get("run") or "-"}  {decision.get("note") or ""}'
                 if decision else ('取り込み済み・採否待ち' if entry.get('status') == 'merged' else entry.get('status')))
        lines.append(f'  前の投稿 {entry["commit"][:8]}: {state}')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('submit')
    p.add_argument('--hypothesis', required=True, help='何を変えて、なぜ速くなる（点が上がる）と考えるか')
    p.add_argument('--expect', required=True, help='期待する観測値（route、SQL、CPUなど、briefとqueryで確かめられるもの）')
    p.add_argument('--verified', required=True, help='ローカルで確かめたこと（build、test、local-check、local-flowなど）')
    p.add_argument('--note')
    p = sub.add_parser('list')
    p.add_argument('--all', action='store_true')
    p = sub.add_parser('show')
    p.add_argument('branch', nargs='?')
    p = sub.add_parser('decide')
    p.add_argument('branch')
    p.add_argument('decision', choices=DECISIONS)
    p.add_argument('--run')
    p.add_argument('--note')
    p.add_argument('--commit', help='判断を付けるcommit（既定は、mainへ取り込んだ未判断のcommit）')
    p = sub.add_parser('withdraw')
    p.add_argument('branch', nargs='?')
    args = parser.parse_args()
    root = main_session.root_repo()
    if args.action == 'submit':
        print(submit(root, args.hypothesis, args.expect, args.verified, args.note))
    elif args.action == 'decide':
        print(decide(root, args.branch, args.decision, args.run, args.note, args.commit))
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
        items = [data for data in items if args.all or data.get('status') in OPEN or undecided(data)]
        print('\n'.join(describe(data) for data in items) if items else '投稿は無い。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
