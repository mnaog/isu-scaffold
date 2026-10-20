#!/usr/bin/env python3
"""Proposals: the queue of changes waiting for main's benchmark, and main's decisions on them.

  proposal.py submit --hypothesis TEXT --expect TEXT --verified TEXT [--based-on RUN] [--note TEXT]
  proposal.py list [--all]                 the queue: measuring, next, waiting, held (--all: decided too)
  proposal.py show [BRANCH]                one proposal (default: this worktree's branch)
  proposal.py wait [--timeout SECONDS]     improve: return once the queue has room for its next proposal
  proposal.py improve                      declare this worktree session improve (/improve)
  proposal.py withdraw [BRANCH]
main only:
  proposal.py pending                      the proposals not handed to main yet, as the benchmark hook does
  proposal.py sync [BRANCH ...]            bring the waiting proposals up to main's branch; stops at a conflict
  proposal.py hold BRANCH --until TEXT [--note TEXT]   out of the queue until the constraint moves there
  proposal.py unhold BRANCH                back into the queue
  proposal.py requeue [BRANCH ...]         measuring back to waiting, after a benchmark that did not measure them
  proposal.py decide BRANCH accepted|rejected|deferred [--run ID] [--note TEXT] [--commit COMMIT]
  proposal.py freeze [--off] [--note TEXT] stop improve's proposals before the end

A person or a Claude Code session working in a worktree (make worktree) commits its change and
submits it from that worktree. The proposal is .local/proposals/<branch>.json in the root
repository: the branch and commit, the hypothesis, the observation it expects, what was checked
locally, the run it was based on, the proposals it is built on, and the files it changes.

The queue is every proposal that still waits for a benchmark: submitted, delivered (main has read
it), needs-sync (it conflicts with main's branch) and merged (main merged it for the next
benchmark). When main starts a benchmark, scripts/bench-hook.py --post marks the merged ones
measuring; they leave the queue and wait for main's decision. Held proposals are out of the queue
too, with the constraint they wait for. The improve session (/improve, declared with `improve`)
may add a proposal only while at most one waits in the whole queue, so what it builds on stays
close to the latest run; it waits for room with `wait`, run in the background. People and other
worktrees are not limited. Submitting a branch that already waits replaces it.

Once submitted, a branch is main's until it is decided. main keeps the queue mergeable while a
benchmark runs: `sync` merges main's branch into each waiting branch in its own worktree
(.local/proposal-sync), and leaves the first conflict there for main to resolve. main may also
decide a proposal without measuring it. A rejected, deferred or withdrawn proposal sends back the
waiting proposals built on it.

When main starts a benchmark in the background, the hook hands it the proposals it has not seen at
their current commit that merge into main's branch without a conflict (git merge-tree); `pending`
does the same while no benchmark runs. Submitting, syncing, holding, deciding and freezing write a
line to .local/progress.log (`proposal <state>`), which main and improve follow with Monitor.

A decision belongs to the commit main merged and measured, including earlier submissions in history.
Acceptance requires a run; merged and measuring proposals remain owed a decision.
"""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import time

SCRIPTS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('main_session', SCRIPTS / 'main-session.py')
main_session = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(main_session)

# Waiting for a benchmark: what the queue counts.
WAITING = ('submitted', 'delivered', 'needs-sync', 'merged')
# Not decided yet.
OPEN = WAITING + ('measuring', 'held')
DECISIONS = ('accepted', 'rejected', 'deferred')
# improve adds a proposal only while at most this many wait in the whole queue.
IMPROVE_ROOM = 1
FULL_EXIT = 3
FROZEN_EXIT = 4
WAIT_INTERVAL_SECONDS = 2
HINT = ('worktreeからの投稿です（mainのbranchへ衝突なく入ることを確かめ済み）。ベンチを待つ間に読み、'
        '次に測るものを選んだら`git merge --no-ff <branch>`して`make build`しておく。'
        '何をどの順で、どう組み合わせて測るかはmainが決める（まとめたら、仮説に投稿ごとの期待を並べ、'
        '`isuscope analyze`を投稿ごとに`--change <branch>`で記録する）。測らずに返してもよい。採否は'
        '`python3 scripts/proposal.py decide <branch> accepted|rejected|deferred --run <短縮ID> --note "理由"`'
        'で投稿者へ返す（isuscopeの`analyze --change`とは別に記録する）。判断はmainへ取り込んで測ったcommitに付く。')
GROUPS = {'measuring': '測定中', 'merged': '次のベンチ', 'delivered': '待ち', 'submitted': '待ち（未読）',
          'needs-sync': '待ち（衝突）', 'held': '保留'}


class Refused(Exception):
    """A request the queue turns down, with the exit code that tells the caller why."""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def progress_emit(root, source, state, message):
    """A line in .local/progress.log; never fails the proposal (progress.py may be absent)."""
    try:
        spec = importlib.util.spec_from_file_location('progress', SCRIPTS / 'progress.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # emit echoes the line on stdout; the hooks that call this print JSON there.
        with contextlib.redirect_stdout(io.StringIO()):
            module.emit(source, state, message, root=root)
    except (OSError, ImportError, AttributeError):
        pass


def progress(root, state, message):
    progress_emit(root, 'proposal', state, message)


def git(*args, cwd=None):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)


def directory(root):
    return Path(root) / '.local/proposals'


def state_dir(root):
    """improve's declaration and the freeze, apart from the proposals (a branch may be named improve)."""
    return Path(root) / '.local/proposal-state'


def file_of(root, branch):
    return directory(root) / (branch.replace('/', '__') + '.json')


def read_json(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def load_all(root):
    items = []
    for item in sorted(directory(root).glob('*.json')) if directory(root).is_dir() else []:
        data = read_json(item)
        if data and data.get('branch'):
            items.append(data)
    return items


def load(root, branch):
    return read_json(file_of(root, branch))


def save(root, data):
    write_json(file_of(root, data['branch']), data)


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def current_branch(cwd=None):
    return git('symbolic-ref', '--quiet', '--short', 'HEAD', cwd=cwd).stdout.strip()


def is_ancestor(root, commit, of):
    return git('merge-base', '--is-ancestor', commit, of, cwd=root).returncode == 0


def improve_sessions(root):
    return (read_json(state_dir(root) / 'improve.json') or {}).get('sessions', {})


def is_improve(root, session, here):
    """A session or worktree that declared itself improve."""
    sessions = improve_sessions(root)
    return bool(session and session in sessions) or any(
        here and entry.get('worktree') == str(here) for entry in sessions.values())


def claim_improve(root, cwd=None):
    here = main_session.toplevel(cwd)
    if here is None or here == Path(root).resolve():
        raise Refused('improveはworktree（準備で作ったworktree `improve`）で動かす。repoのrootはmainの作業場所。')
    agent, session = main_session.current_session()
    if not session:
        raise Refused('セッションIDが分からない。CLAUDE_CODE_SESSION_IDかCODEX_THREAD_IDのある環境で実行する。')
    path = state_dir(root) / 'improve.json'
    data = read_json(path) or {}
    data.setdefault('sessions', {})[session] = {'agent': agent, 'worktree': str(here), 'claimed_at': now()}
    write_json(path, data)
    progress_emit(root, 'improve', 'started', f'{here} ({agent} {session})')
    return (f'このセッション（{agent} {session}）をimproveにした。キューの待ちが{IMPROVE_ROOM}件以下のときだけ投稿できる'
            f'（今は{queue_count(root)}件）。空きは`python3 scripts/proposal.py wait`で待つ。')


def frozen(root):
    return read_json(state_dir(root) / 'freeze.json')


def freeze(root, off=False, note=None):
    path = state_dir(root) / 'freeze.json'
    if off:
        path.unlink(missing_ok=True)
        progress(root, 'unfrozen', note or '')
        return 'improveの投稿を再び受け付ける。'
    write_json(path, {'at': now(), 'note': note or ''})
    progress_emit(root, 'main', 'freeze', note or '')
    return 'improveの新しい投稿を止めた（`wait`も止まる）。人間の投稿は受け付ける。'


def queue_count(root, items=None, without=None):
    items = load_all(root) if items is None else items
    return sum(1 for data in items if data.get('status') in WAITING and data.get('branch') != without)


def submit(root, hypothesis, expect, verified, note=None, cwd=None, based_on=None):
    here = main_session.toplevel(cwd)
    if here is None or here == Path(root).resolve():
        raise Refused('投稿はworktree（make worktree BRANCH=...）から行う。repoのrootはmainの作業場所。')
    branch = current_branch(cwd)
    base = main_session.main_branch(root)
    if not branch or branch == base:
        raise Refused(f'mainのbranch（{base}）ではなく、自分のbranchから投稿する。')
    # The conversation log keeps being written while the session runs; it is not part of the change.
    dirty = [line for line in git('status', '--porcelain', cwd=cwd).stdout.splitlines()
             if line and not line[3:].startswith('docs/agent-history/')]
    if dirty:
        raise Refused('commitしていない変更がある。commitしてから投稿する:\n' + '\n'.join(dirty[:10]))
    ahead = git('rev-list', '--count', f'{base}..HEAD', cwd=cwd).stdout.strip()
    if ahead in ('', '0'):
        raise Refused(f'{base}に無いcommitが無い。変更をcommitしてから投稿する。')
    previous = load(root, branch) or {}
    if previous.get('status') == 'measuring':
        raise Refused(f'{branch}は測定中。採否が返るまで投稿し直さない（直すなら新しいbranchで投稿する）。')
    agent, session = main_session.current_session()
    items = load_all(root)
    improve = is_improve(root, session, here)
    if improve:
        if frozen(root):
            raise Refused('mainが終了前の固定（freeze）に入った。新しい変更は投稿しない。', FROZEN_EXIT)
        waiting = queue_count(root, items, without=branch)
        # Replacing a proposal that already waits adds nothing to the queue.
        if previous.get('status') not in WAITING and waiting > IMPROVE_ROOM:
            raise Refused(f'キューの待ちが{waiting}件ある。improveは待ちが{IMPROVE_ROOM}件以下のときだけ投稿できる。'
                          '`python3 scripts/proposal.py wait`をbackgroundで起動して空きを待ち、その間に次の変更を進める。',
                          FULL_EXIT)
    commit = git('rev-parse', 'HEAD', cwd=cwd).stdout.strip()
    files = git('diff', '--name-only', f'{base}...HEAD', cwd=cwd).stdout.split()
    # Built on another proposal that main's branch does not hold yet: it stands or falls with it.
    depends_on = sorted(other['branch'] for other in items
                        if other['branch'] != branch and other.get('status') in OPEN
                        and is_ancestor(root, other['commit'], commit) and not is_ancestor(root, other['commit'], base))
    data = {'branch': branch, 'commit': commit, 'base': base, 'worktree': str(here),
            'hypothesis': hypothesis, 'expect': expect, 'verified': verified, 'note': note or '',
            'based_on': based_on or '', 'depends_on': depends_on, 'files': files,
            'author': {'agent': agent, 'session': session, 'role': 'improve' if improve else 'worktree'},
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
    progress(root, 'submitted', f'{branch} {commit[:8]}: {hypothesis[:120]}')
    waiting = queue_count(root)
    lines = [f'{branch}（{commit[:8]}）を投稿した。キューの待ちは{waiting}件。mainが次にベンチを起動したときに渡る。'
             '状態は`python3 scripts/proposal.py show`。投稿したbranchは採否が出るまでmainのもの（commitしない）。']
    if depends_on:
        lines.append(f'取り込まれていない投稿の上に積んでいる: {", ".join(depends_on)}（それが返されると一緒に返る）。')
    return '\n'.join(lines)


def merge_check(root, base, commit):
    """None when it merges cleanly, else the conflicting files ([] when git cannot tell)."""
    result = git('merge-tree', '--write-tree', '--name-only', '--no-messages', base, commit, cwd=root)
    if result.returncode == 0:
        return None
    if result.returncode == 1:
        return [line for line in result.stdout.splitlines()[1:] if line.strip()]
    return []


def refresh(root, data, base=None):
    """Mark a waiting proposal merged once main's branch holds it, or needs-sync while it conflicts."""
    base = base or main_session.main_branch(root)
    if data.get('status') not in ('submitted', 'delivered', 'needs-sync'):
        return data
    if is_ancestor(root, data['commit'], base):
        data.update(status='merged', merged_at=now())
        save(root, data)
        return data
    conflicts = merge_check(root, base, data['commit'])
    if conflicts is not None:
        if data.get('status') != 'needs-sync':
            data.update(status='needs-sync', conflicts=conflicts, checked_at=now())
            save(root, data)
    elif data.get('status') == 'needs-sync':
        data.update(status='delivered' if data.get('delivered') else 'submitted', conflicts=[])
        save(root, data)
    return data


def start_measuring(root):
    """At a benchmark's start: the proposals main merged leave the queue and wait for the decision."""
    base = main_session.main_branch(root)
    started = []
    for data in load_all(root):
        data = refresh(root, data, base)
        if data.get('status') == 'merged':
            data.update(status='measuring', measuring_since=now())
            save(root, data)
            started.append(data['branch'])
    if started:
        progress(root, 'measuring', ', '.join(started))
    return started


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
    return [entry['commit'] for entry in open_entries(data) if entry.get('status') in ('merged', 'measuring')]


def deliver(root, session, at=None):
    """The proposals this main session has not been handed at their current commit, as hook context."""
    base = main_session.main_branch(root)
    fresh, held, waiting = [], [], []
    for data in load_all(root):
        data = refresh(root, data, base)
        # Every benchmark until main decides: a merged proposal is measured, not finished.
        waiting.extend(f'{data["branch"]}（{commit[:8]}）' for commit in undecided(data))
        if data.get('status') == 'needs-sync':
            # Told once per commit, so main knows to resolve it with `sync`.
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
            f'### {data["branch"]}（{data["commit"][:8]}、投稿 {age(data.get("submitted_at"), at)}、'
            f'{author.get("role") or "worktree"} {author.get("agent") or "?"}）',
            f'- 仮説: {data.get("hypothesis")}',
            f'- 期待する観測: {data.get("expect")}',
            f'- 確かめたこと: {data.get("verified")}',
            *([f'- 根拠のrun: {data["based_on"]}'] if data.get('based_on') else []),
            *([f'- 積んでいる投稿: {", ".join(data["depends_on"])}'] if data.get('depends_on') else []),
            *([f'- 補足: {data["note"]}'] if data.get('note') else []),
            f'- 変えたfile: {shown or "(なし)"}',
            f'- 中身: `git log --stat {base}..{data["branch"]}`、取り込み: `git merge --no-ff {data["branch"]}`',
        ]))
        data.setdefault('delivered', {})[session] = data['commit']
        data['status'] = 'delivered'
        data['delivered_at'] = now()
        save(root, data)
    if held:
        parts.append(f'mainのbranchと衝突する投稿: {", ".join(held)}。ベンチを待つ間に'
                     '`python3 scripts/proposal.py sync`で取り込み、衝突を解消する。')
    if waiting:
        parts.append(f'取り込んだが採否を返していない投稿: {", ".join(waiting)}。測ったrunを付けて'
                     '`python3 scripts/proposal.py decide <branch> accepted|rejected|deferred --run <短縮ID>`で返す。')
    return '\n\n'.join(parts)


def send_back_dependents(root, branch, reason):
    """The waiting proposals built on one that will not be taken go back to their authors."""
    returned = []
    for data in load_all(root):
        if branch in (data.get('depends_on') or []) and data.get('status') in WAITING + ('held',):
            data.update(status='deferred', decision={'decision': 'deferred', 'run': '', 'at': now(),
                                                     'note': f'積んでいた{branch}が{reason}', 'commit': data['commit'],
                                                     'by': {'agent': 'proposal.py', 'session': ''}})
            save(root, data)
            returned.append(data['branch'])
            progress(root, 'deferred', f'{data["branch"]} run -: 積んでいた{branch}が{reason}')
    return returned


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
    progress(root, decision, f'{branch} {target[:8]} run {run or "-"}: {note or ""}')
    if entry is not data:
        return (f'{branch}の{target[:8]}（{base}へ取り込んだcommit）を{decision}にした。'
                f'今の投稿{data["commit"][:8]}はその後に出たもので、判断は付けていない。')
    lines = [f'{branch}（{target[:8]}）を{decision}にした。' + ('' if run else '（ベンチにかけずに返した）')]
    if decision != 'accepted' and (returned := send_back_dependents(root, branch, decision)):
        lines.append(f'積んでいた投稿も返した: {", ".join(returned)}')
    return '\n'.join(lines)


def hold(root, branch, until, note=None):
    data = load(root, branch)
    if not data or data.get('status') not in WAITING:
        raise Refused(f'{branch}はキューで待っていない（`python3 scripts/proposal.py list`）。')
    data.update(status='held', hold={'until': until, 'note': note or '', 'at': now(), 'from': data.get('status')})
    save(root, data)
    progress(root, 'held', f'{branch}: {until}')
    return f'{branch}を保留にした（キューに数えない）。「{until}」になったら`unhold`で戻す。'


def unhold(root, branch):
    data = load(root, branch)
    if not data or data.get('status') != 'held':
        raise Refused(f'{branch}は保留ではない。')
    data.update(status='delivered' if data.get('delivered') else 'submitted')
    save(root, data)
    refresh(root, data)
    progress(root, 'unheld', branch)
    return f'{branch}をキューへ戻した。'


def requeue(root, branches=None):
    """Measuring proposals back to the queue, when the benchmark did not start or did not measure them."""
    base = main_session.main_branch(root)
    back = []
    for data in load_all(root):
        if data.get('status') != 'measuring' or (branches and data['branch'] not in branches):
            continue
        status = 'merged' if is_ancestor(root, data['commit'], base) else (
            'delivered' if data.get('delivered') else 'submitted')
        data.update(status=status)
        data.pop('measuring_since', None)
        save(root, data)
        back.append(data['branch'])
    if not back:
        raise Refused('測定中の投稿は無い。' if not branches else f'{", ".join(branches)}は測定中ではない。')
    progress(root, 'requeued', ', '.join(back))
    return f'キューへ戻した: {", ".join(back)}'


def withdraw(root, branch):
    data = load(root, branch)
    if not data:
        raise Refused(f'{branch}の投稿は無い。')
    data.update(status='withdrawn', withdrawn_at=now())
    save(root, data)
    progress(root, 'withdrawn', branch)
    returned = send_back_dependents(root, branch, 'withdrawn')
    return f'{branch}の投稿を取り下げた。' + (f'積んでいた投稿も返した: {", ".join(returned)}' if returned else '')


def worktree_branches(root):
    """Branch -> path of the worktree that has it checked out."""
    result, path = {}, None
    for line in git('worktree', 'list', '--porcelain', cwd=root).stdout.splitlines():
        if line.startswith('worktree '):
            path = line[len('worktree '):]
        elif line.startswith('branch refs/heads/'):
            result[line[len('branch refs/heads/'):]] = path
    return result


def sync_dir(root):
    return Path(root) / '.local/proposal-sync'


def sync(root, branches=None):
    """Merge main's branch into each waiting proposal, in a worktree of main's own; stop at a conflict."""
    base = main_session.main_branch(root)
    work = sync_dir(root)
    if not (work / '.git').exists():
        added = git('worktree', 'add', '--quiet', '--detach', str(work), base, cwd=root)
        if added.returncode != 0:
            raise Refused(f'同期用のworktreeを作れない: {added.stderr.strip()}')
    lines = []
    # A conflict left from the last sync: finish it before anything else.
    if git('rev-parse', '-q', '--verify', 'MERGE_HEAD', cwd=work).returncode == 0:
        branch = current_branch(work)
        unmerged = git('diff', '--name-only', '--diff-filter=U', cwd=work).stdout.split()
        if unmerged:
            raise Refused(f'{branch}の衝突を解消中（{work}）: {", ".join(unmerged)}。解消して`git add`してから、'
                          f'もう一度`python3 scripts/proposal.py sync`。やめるなら{work}で`git merge --abort`。')
        commit = git('commit', '--quiet', '--no-edit', cwd=work)
        if commit.returncode != 0:
            raise Refused(f'{branch}の衝突の解消をcommitできない: {commit.stderr.strip()}')
        lines.append(record_sync(root, branch, work, base))
        git('switch', '--quiet', '--detach', cwd=work)
    held_by = worktree_branches(root)
    wanted = set(branches or [])
    candidates = []
    for data in load_all(root):
        if (wanted and data['branch'] not in wanted) or data.get('status') not in ('submitted', 'delivered',
                                                                                    'needs-sync', 'held'):
            continue
        data = refresh(root, data, base)
        if data.get('status') != 'merged':
            candidates.append((merge_check(root, base, data['commit']) is not None,
                               data.get('first_submitted_at') or '', data))
    # The clean ones first, so one conflict left for main does not hold the rest back.
    for _, _, data in sorted(candidates, key=lambda item: item[:2]):
        branch = data['branch']
        tip = git('rev-parse', '--verify', '-q', f'refs/heads/{branch}', cwd=root).stdout.strip()
        if tip != data['commit']:
            lines.append(f'- {branch}: 投稿の後にbranchが動いた（{tip[:8] or "消えた"}）。投稿者に確かめる。')
            continue
        if is_ancestor(root, base, data['commit']):
            lines.append(f'- {branch}: 最新')
            continue
        if held_by.get(branch) and Path(held_by[branch]).resolve() != work.resolve():
            lines.append(f'- {branch}: {held_by[branch]}でcheckoutされている。投稿者が別のbranchへ移るまで同期できない。')
            continue
        switched = git('switch', '--quiet', branch, cwd=work)
        if switched.returncode != 0:
            lines.append(f'- {branch}: 同期用のworktreeへcheckoutできない: {switched.stderr.strip()}')
            continue
        merged = git('merge', '--quiet', '--no-ff', '--no-edit', '-m', f'Merge {base} into {branch}', base, cwd=work)
        if merged.returncode != 0:
            unmerged = git('diff', '--name-only', '--diff-filter=U', cwd=work).stdout.split()
            if not unmerged:
                git('merge', '--abort', cwd=work)
                git('switch', '--quiet', '--detach', cwd=work)
                lines.append(f'- {branch}: mergeできない: {merged.stderr.strip() or merged.stdout.strip()}')
                continue
            data.update(status='needs-sync', conflicts=unmerged, checked_at=now())
            save(root, data)
            lines.append(f'- {branch}: 衝突（{", ".join(unmerged)}）。{work}で解消して`git add`し、もう一度'
                         '`python3 scripts/proposal.py sync`。処理の意味がぶつかるなら`git merge --abort`して投稿者へ返す。')
            return '\n'.join(lines)
        lines.append(record_sync(root, branch, work, base))
        git('switch', '--quiet', '--detach', cwd=work)
    return '\n'.join(lines) if lines else '同期する投稿は無い。'


def record_sync(root, branch, work, base):
    """The proposal now points at the merge; main keeps it as read, since main made it."""
    data = load(root, branch)
    old, new = data['commit'], git('rev-parse', 'HEAD', cwd=work).stdout.strip()
    delivered = {who: (new if commit == old else commit) for who, commit in data.get('delivered', {}).items()}
    status = data['status']
    if status == 'needs-sync':
        status = 'delivered' if delivered else 'submitted'
    data.update(commit=new, status=status, delivered=delivered, conflicts=[], synced_at=now(),
                files=git('diff', '--name-only', f'{base}...{new}', cwd=root).stdout.split())
    data.setdefault('history', []).append({'commit': old, 'status': 'synced', 'at': now()})
    save(root, data)
    progress(root, 'synced', f'{branch} {new[:8]}')
    return f'- {branch}: {base}を取り込んだ（{new[:8]}）'


def wait(root, timeout=3600, interval=WAIT_INTERVAL_SECONDS):
    """Return once the queue has room for improve's next proposal, or when main freezes."""
    deadline = time.time() + timeout
    while True:
        if frozen(root):
            raise Refused('mainが終了前の固定（freeze）に入った。新しい変更は投稿しない。', FROZEN_EXIT)
        waiting = queue_count(root)
        if waiting <= IMPROVE_ROOM:
            return f'キューの待ちは{waiting}件。投稿できる。'
        if time.time() >= deadline:
            raise Refused(f'{timeout}秒待っても、キューの待ちが{waiting}件のまま。', 2)
        time.sleep(interval)


def queue(root):
    """The queue for people and the board: read as stored, without touching git or the files."""
    items = load_all(root)
    order = {'measuring': 0, 'merged': 1, 'needs-sync': 2, 'delivered': 2, 'submitted': 2, 'held': 3}
    shown = sorted((data for data in items if data.get('status') in OPEN),
                   key=lambda data: (order[data['status']], data.get('first_submitted_at') or ''))
    fields = ('branch', 'status', 'commit', 'hypothesis', 'expect', 'based_on', 'depends_on', 'note',
              'first_submitted_at', 'submitted_at', 'measuring_since', 'conflicts', 'hold', 'files')
    return {'waiting': queue_count(root, items), 'room': IMPROVE_ROOM, 'frozen': frozen(root),
            'items': [{**{key: data.get(key) for key in fields}, 'group': GROUPS[data['status']],
                       'author': (data.get('author') or {}).get('role') or 'worktree',
                       'agent': (data.get('author') or {}).get('agent') or ''} for data in shown]}


def describe(data):
    lines = [f'{data["branch"]}  {data.get("status")}  {data["commit"][:8]}  投稿 {age(data.get("submitted_at"))}',
             f'  仮説: {data.get("hypothesis")}', f'  期待する観測: {data.get("expect")}',
             f'  確かめたこと: {data.get("verified")}']
    if data.get('based_on'):
        lines.append(f'  根拠のrun: {data["based_on"]}')
    if data.get('depends_on'):
        lines.append(f'  積んでいる投稿: {", ".join(data["depends_on"])}')
    if data.get('status') == 'needs-sync':
        lines.append(f'  mainのbranchと衝突: {", ".join(data.get("conflicts") or []) or "(詳細不明)"}。'
                     'mainが`python3 scripts/proposal.py sync`で解消する。')
    if data.get('status') == 'held':
        lines.append(f'  保留: {(data.get("hold") or {}).get("until")}  {(data.get("hold") or {}).get("note") or ""}')
    if data.get('decision'):
        decision = data['decision']
        lines.append(f'  mainの判断: {decision.get("decision")}  run {decision.get("run") or "-"}  {decision.get("note") or ""}')
    elif data.get('status') in ('merged', 'measuring'):
        lines.append('  mainへ取り込み済み。採否はまだ返っていない。')
    for entry in reversed(data.get('history') or []):
        decision = entry.get('decision') or {}
        state = (f'{decision.get("decision")}  run {decision.get("run") or "-"}  {decision.get("note") or ""}'
                 if decision else ('取り込み済み・採否待ち' if entry.get('status') in ('merged', 'measuring') else entry.get('status')))
        lines.append(f'  前の投稿 {entry["commit"][:8]}: {state}')
    return '\n'.join(lines)


def listing(root, everything=False):
    base = main_session.main_branch(root)
    items = [refresh(root, data, base) for data in load_all(root)]
    head = (f'キューの待ち: {queue_count(root, items)}件（improveは{IMPROVE_ROOM}件以下のときに投稿できる）'
            + ('。freeze中' if frozen(root) else ''))
    sections = []
    for status in ('measuring', 'merged', 'needs-sync', 'delivered', 'submitted', 'held'):
        chosen = sorted((data for data in items if data.get('status') == status),
                        key=lambda data: data.get('first_submitted_at') or '')
        if chosen:
            sections.append(f'## {GROUPS[status]}\n' + '\n'.join(describe(data) for data in chosen))
    owed = [data for data in items if data.get('status') not in OPEN and undecided(data)]
    if owed:
        sections.append('## 採否待ちの履歴\n' + '\n'.join(describe(data) for data in owed))
    if everything:
        closed = [data for data in items if data.get('status') not in OPEN]
        if closed:
            sections.append('## 終わったもの\n' + '\n'.join(describe(data) for data in closed))
    return '\n\n'.join([head, *sections]) if sections else head + '\n投稿は無い。'


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
    p = sub.add_parser('wait')
    p.add_argument('--timeout', type=int, default=3600)
    sub.add_parser('improve')
    sub.add_parser('pending')
    p = sub.add_parser('sync')
    p.add_argument('branches', nargs='*')
    p = sub.add_parser('hold')
    p.add_argument('branch')
    p.add_argument('--until', required=True, help='測る条件（例: app3のアプリのCPUが最大の制約になったら）')
    p.add_argument('--note')
    p = sub.add_parser('unhold')
    p.add_argument('branch')
    p = sub.add_parser('requeue')
    p.add_argument('branches', nargs='*')
    p = sub.add_parser('decide')
    p.add_argument('branch')
    p.add_argument('decision', choices=DECISIONS)
    p.add_argument('--run')
    p.add_argument('--note')
    p.add_argument('--commit', help='判断を付けるcommit（既定は、mainへ取り込んだ未判断のcommit）')
    p = sub.add_parser('freeze')
    p.add_argument('--off', action='store_true')
    p.add_argument('--note')
    p = sub.add_parser('withdraw')
    p.add_argument('branch', nargs='?')
    args = parser.parse_args()
    root = main_session.root_repo()
    _, session = main_session.current_session()
    try:
        if args.action in ('pending', 'sync', 'hold', 'unhold', 'requeue', 'decide', 'freeze') \
                and main_session.is_main(root, session) is False:
            raise Refused('これはmain（/mainで宣言したセッション）の操作。')
        if args.action == 'submit':
            print(submit(root, args.hypothesis, args.expect, args.verified, args.note, based_on=args.based_on))
        elif args.action == 'pending':
            print(deliver(root, session or 'unknown') or '新しい投稿は無い。')
        elif args.action == 'decide':
            print(decide(root, args.branch, args.decision, args.run, args.note, args.commit))
        elif args.action == 'hold':
            print(hold(root, args.branch, args.until, args.note))
        elif args.action == 'unhold':
            print(unhold(root, args.branch))
        elif args.action == 'requeue':
            print(requeue(root, args.branches))
        elif args.action == 'sync':
            print(sync(root, args.branches))
        elif args.action == 'wait':
            print(wait(root, args.timeout))
        elif args.action == 'improve':
            print(claim_improve(root))
        elif args.action == 'freeze':
            print(freeze(root, args.off, args.note))
        elif args.action == 'withdraw':
            print(withdraw(root, args.branch or current_branch()))
        elif args.action == 'show':
            branch = args.branch or current_branch()
            data = load(root, branch)
            if not data:
                raise Refused(f'{branch}の投稿は無い。')
            print(describe(refresh(root, data)))
        else:
            print(listing(root, args.all))
    except Refused as refused:
        print(str(refused), file=sys.stderr)
        return refused.code
    return 0


if __name__ == '__main__':
    sys.exit(main())
