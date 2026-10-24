#!/usr/bin/env python3
"""Proposals: the queue of changes waiting for main's benchmark, and main's decisions on them.

  proposal.py submit --hypothesis TEXT --expect TEXT [--verified TEXT] [--based-on RUN] [--note TEXT]
  proposal.py list [--all]                 the queue: measuring, next, waiting, held (--all: decided too)
  proposal.py show [BRANCH]                one proposal (default: this worktree's branch)
  proposal.py wait [--timeout SECONDS]     improve: return once the queue has room for its next proposal
  proposal.py improve                      declare this worktree session improve (/improve)
  proposal.py withdraw [BRANCH]
  proposal.py working TEXT | --done        show on the board what this worktree is building now
  proposal.py hypothesis                   the run hypothesis for the proposals merged for the next benchmark
main only:
  proposal.py pending                      the proposals not handed to main yet, as the benchmark hook does
  proposal.py sync [BRANCH ...]            bring the waiting proposals up to main's branch; stops at a conflict
  proposal.py hold BRANCH --until TEXT [--note TEXT]   out of the queue until the constraint moves there
  proposal.py unhold BRANCH                back into the queue
  proposal.py requeue [BRANCH ...]         measuring back to waiting, after a benchmark that did not measure them
  proposal.py decide BRANCH accepted|rejected|deferred [--run ID] [--note TEXT] [--commit COMMIT]
  proposal.py next RUN [--verdict V] --note TEXT [--decide BRANCH=STATUS[:NOTE] ...] [--merge BRANCH ...] [--requeue]
                                           a finished run's decisions, then the next proposals merged
  proposal.py freeze [--off] [--note TEXT] stop improve's proposals before the end

A person or a Claude Code session working in a worktree (make worktree) commits its change and
submits it from that worktree. The proposal is .local/proposals/<branch>.json in the root
repository: the branch and commit, the hypothesis, the observation it expects, what was checked
locally, the run it was based on, the proposals it is built on, and the files it changes.

The queue is every proposal that still waits for a benchmark: submitted, delivered (main has read
it), needs-sync (it conflicts with main's branch) and merged (main merged it for the next
benchmark). When main starts a benchmark, the hook (scripts/bench-hook.py started) marks the merged ones
measuring; they leave the queue and wait for main's decision. Held proposals are out of the queue
too, with the constraint they wait for. The improve session (/improve, declared with `improve`)
may add a proposal only while at most one waits in the whole queue, so what it builds on stays
close to the latest run; it waits for room with `wait`, run in the background. People and other
worktrees are not limited. Submitting a branch that already waits replaces it.

Once submitted, a branch is main's until it is decided. main keeps the queue mergeable while a
benchmark runs: `sync` merges main's branch into each waiting branch in its own worktree
(.local/proposal-sync), and leaves the first conflict there for main to resolve. Nothing goes back
to its author: main fixes what it can (a conflict, a build that breaks once merged) and rejects what
it cannot, or what needs no benchmark to reject. A rejected, deferred or withdrawn proposal takes the
waiting proposals built on it out of the queue. `submit` detaches the author's worktree from the
branch it submitted, so `sync` can move that branch.

When main starts a benchmark in the background, the hook hands it the proposals it has not seen at
their current commit that merge into main's branch without a conflict (git merge-tree); `pending`
does the same while no benchmark runs. Submitting, syncing, holding, deciding and freezing write a
line to .local/progress.log (`proposal <state>`), which main and improve follow with Monitor.

A decision belongs to the commit main merged and measured, including earlier submissions in history.
Acceptance requires a run; merged and measuring proposals remain owed a decision.

`next` takes main from a finished run to the next benchmark in one call: it records the run's verdict
and each decision in isuscope (the short analysis main writes before the next benchmark), copies them
onto the proposals, which takes the waiting proposals built on a rejected one out of the queue,
reverts what was not taken, checks the next proposals again and merges them. The proposals a run
measured are those merged into the commit it deployed. Each step looks at what is already done, so
the same command continues after a failure. improve does not hear of these short analyses (`main
analyzed`); it hears of the detailed one main appends while the next benchmark runs.

Before there is anything to submit, a worktree (improve, a person in worktree `human1`) says what it
is building with `working`; the board shows who works on what until it submits (which clears it) or
says `working --done`. `hypothesis` writes the hypothesis main gives `isuscope run` from the merged
proposals' own hypothesis and expected observation, so the run reads as the queue entry did.
"""
import argparse
import contextlib
import fcntl
from datetime import datetime
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

SCRIPTS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('main_session', SCRIPTS / 'main-session.py')
main_session = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(main_session)
_spec = importlib.util.spec_from_file_location('isuscope_sql', SCRIPTS / 'isuscope_sql.py')
isuscope_sql = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(isuscope_sql)

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
        '次に測るものを決めておく（まだmergeしない。buildは先buildが済ませる）。ベンチが終わったら、そのrunの採否と一緒に'
        '`python3 scripts/proposal.py next RUN --verdict ... --note "..." --decide <測った投稿>=<採否> --merge <次の投稿>`'
        'で取り込む（採否を先に投稿へ写し、積んでいた投稿を外してから取り込むため）。'
        '何をどの順で、どう組み合わせて測るかはmainが決める（まとめるなら--mergeを並べ、仮説には投稿ごとの期待が並ぶ）。'
        '測るまでもなく棄却できるなら測らずに棄却してよい（`python3 scripts/proposal.py decide <branch> rejected --note "..."`）。'
        '取り込んでbuild・deployが通らなければmainが直し、直せなければ棄却する（投稿者へ戻さない）。'
        '判断はmainへ取り込んで測ったcommitに付く。')
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
    # The benchmark's hypothesis and the background sync can write the same proposal at once.
    temp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
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


def own_branch(root, cwd=None):
    """This worktree's branch, or, once `submit` left it, the branch it submitted last."""
    branch = current_branch(cwd)
    if branch:
        return branch
    here = main_session.toplevel(cwd)
    mine = [data for data in load_all(root) if here and data.get('worktree') == str(here)]
    return max(mine, key=lambda data: data.get('submitted_at') or '')['branch'] if mine else ''


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
    lines = ['improveの新しい投稿を止めた（`wait`も止まる）。人間の投稿は受け付ける。']
    # The last runs often keep only the line `next` recorded: the end is when their detailed analysis is written.
    try:
        if owed := analyses_owed(root):
            lines.append('詳しい分析がまだのrun: ' + ', '.join(short_id(run) for run in owed)
                         + '。終了前に`isuscope analyze RUN <判定> --analysis "..."`で追記する。')
    except isuscope_sql.Unavailable:
        pass
    return '\n'.join(lines)


def working_dir(root):
    return state_dir(root) / 'working'


def worktree_name(root, here):
    return main_session.worktree_name(root, here)


def working(root, what=None, done=False, cwd=None):
    here = main_session.toplevel(cwd)
    if here is None or here == Path(root).resolve():
        raise Refused('作業中の宣言はworktree（improve、human、make worktree）から行う。mainは実装しない。')
    name = worktree_name(root, here)
    path = working_dir(root) / f'{name}.json'
    agent, session = main_session.current_session()
    role = 'improve' if is_improve(root, session, here) else 'worktree'
    if done:
        path.unlink(missing_ok=True)
        progress_emit(root, role if role == 'improve' else 'worktree', 'idle', name)
        return f'{name}の作業中の表示を消した。'
    if not what:
        raise Refused('何をしているか（狙う制約と変更）を書く。')
    write_json(path, {'name': name, 'role': role, 'agent': agent, 'session': session, 'worktree': str(here),
                      'branch': current_branch(cwd), 'what': what, 'started_at': now()})
    progress_emit(root, role if role == 'improve' else 'worktree', 'working', f'{name} {current_branch(cwd)}: {what}')
    return f'{name}の作業中にした（ボードの上部に出る。投稿すると消える）: {what}'


def lane_working(root, name, worktree, branch, what):
    """A lane (scripts/lane.py) builds `what` in its own worktree; lane.py clears it when the lane ends."""
    write_json(working_dir(root) / f'lane-{name}.json',
               {'name': name, 'role': 'lane', 'agent': 'claude -p', 'session': None, 'worktree': str(worktree),
                'branch': branch, 'what': what, 'started_at': now()})


def lane_idle(root, name):
    (working_dir(root) / f'lane-{name}.json').unlink(missing_ok=True)


def lane_running(root, name):
    state = (read_json(Path(root) / '.local/lanes' / str(name) / 'status.json') or {}).get('state')
    return state in ('preparing', 'running')


def working_now(root):
    """Who builds what now, oldest first; a worktree that is gone no longer works on anything, and a lane
    only while it runs (its runner may have died without clearing it)."""
    entries = []
    for path in sorted(working_dir(root).glob('*.json')) if working_dir(root).is_dir() else []:
        data = read_json(path)
        if not data or not Path(data.get('worktree') or '').is_dir():
            continue
        if data.get('role') == 'lane' and not lane_running(root, data.get('name')):
            continue
        entries.append({key: data.get(key) for key in ('name', 'role', 'agent', 'branch', 'what', 'started_at')})
    return sorted(entries, key=lambda entry: entry.get('started_at') or '')


def run_hypothesis(root):
    """The hypothesis for `isuscope run`, from the proposals main merged for the next benchmark.

    Measuring ones count too: started in the background in the same call as the merge, the benchmark
    hook marks them measuring before this runs (eleventh lost two benchmarks to an empty hypothesis),
    and one left measuring by a run without a decision is measured again on the next one."""
    base = main_session.main_branch(root)
    merged = [data for data in (refresh(root, data, base) for data in load_all(root))
              if data.get('status') in ('merged', 'measuring')]
    if not merged:
        raise Refused('次のベンチへ取り込んだ投稿が無い（`git merge --no-ff <branch>`の後に使う）。')
    merged.sort(key=lambda data: data.get('first_submitted_at') or '')
    parts = [f'{data["branch"]}: {data.get("hypothesis") or ""}' + (f' 期待: {data["expect"]}' if data.get('expect') else '')
             for data in merged]
    return ' / '.join(parts)


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
        raise Refused(f'{branch}は測定中。採否が出るまで投稿し直さない（直すなら新しいbranchで投稿する）。')
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
    # sync moves a waiting proposal to a merge commit; a branch built before that holds an earlier one.
    depends_on = sorted(other['branch'] for other in items
                        if other['branch'] != branch and other.get('status') in OPEN
                        and any(is_ancestor(root, c, commit) and not is_ancestor(root, c, base)
                                for c in [other['commit'], *(h['commit'] for h in other.get('history') or [])]))
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
    start_prebuild(root, branch)
    # Submitted, it shows in the queue; it no longer needs saying what this worktree works on.
    declared = working_dir(root) / f'{worktree_name(root, here)}.json'
    was_working = declared.is_file()
    declared.unlink(missing_ok=True)
    waiting = queue_count(root)
    # The branch is main's now: leave it, so main can bring it up to its branch while it waits.
    detached = git('switch', '--quiet', '--detach', cwd=cwd).returncode == 0
    lines = [f'{branch}（{commit[:8]}）を投稿した。キューの待ちは{waiting}件。mainが次にベンチを起動したときに渡る。'
             '状態は`python3 scripts/proposal.py show`。投稿したbranchは採否が出るまでmainのもの'
             + ('で、このworktreeはbranchから外した（HEADは同じcommit）。次の変更は新しいbranchで作る。' if detached
                else '（commitしない）。このworktreeをbranchから外せなかったので、`git switch --detach`で外す。')]
    if depends_on:
        lines.append(f'取り込まれていない投稿の上に積んでいる: {", ".join(depends_on)}（それが棄却・取り下げになると一緒に外れる）。')
    if not was_working:
        lines.append('作り始めたときに`python3 scripts/proposal.py working "<狙う制約と変更>"`で宣言していなかったようです。'
                     '次の変更からは、作り始めたら宣言してください（ボードの「作業中」に出て、誰が何をしているか分かる）。')
    return '\n'.join(lines)


def start_prebuild(root, branch):
    """Build and test the proposal on main's branch now, in the background (scripts/prebuild.py), so main
    finds it built when it merges it, and hears before that if it conflicts or fails its tests."""
    script = Path(root) / 'scripts/prebuild.py'
    if os.environ.get('PROPOSAL_AUTO_PREBUILD') == '0' or not script.is_file():
        return
    log = Path(root) / '.local/prebuild.log'
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a') as out:
            subprocess.Popen([sys.executable, str(script), '--proposal', branch], cwd=root, stdin=subprocess.DEVNULL,
                             stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError:
        pass  # the prebuild at the next benchmark covers it


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


def change_id(branch):
    """The isuscope change a proposal is recorded under: `analyze --change <this>`."""
    return re.sub(r'[^A-Za-z0-9_-]', '-', branch)[:100]


def instant(stamp):
    """A time from this script (local, %z) or from isuscope (UTC, fractions, Z)."""
    try:
        return datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%S%z')
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(re.sub(r'\.(\d{6})\d*', r'.\1', stamp.replace('Z', '+00:00')))
    except (AttributeError, ValueError):
        return None


def latest_change_decision(root, branch):
    """The last decision isuscope recorded for the proposal's change, with the run it measured.

    `isuscope analyze RUN --change` cites the run and its comparison base. The measured run is the one
    analysed against the other; run IDs grow with time, so the newer one when that cannot be told
    (thirteenth took the older one, the base, for 33 of its 42 decisions)."""
    # change_id leaves only [A-Za-z0-9_-], so it is safe inside the SQL string.
    rows = isuscope_sql.rows(root, f"""
        SELECT d.created_at, d.status, d.reason, c.run_id
        FROM change_decisions d LEFT JOIN change_decision_runs c ON c.decision_id = d.id
        WHERE d.change_id = '{change_id(branch)}'
        ORDER BY d.created_at DESC, d.id DESC,
                 EXISTS (SELECT 1 FROM run_analyses a JOIN change_decision_runs o ON o.run_id = a.base_run_id
                         WHERE o.decision_id = c.decision_id AND o.run_id <> c.run_id AND a.run_id = c.run_id) DESC,
                 c.run_id DESC
        LIMIT 1""")
    return rows[0] if rows and instant(rows[0]['created_at']) else None


def adopt_decisions(root):
    """Copy what main decided with `isuscope analyze --change <branch> --decision ...` onto the proposals
    being measured, so main writes each decision once. A provisional change keeps its code: accepted."""
    adopted = []
    for data in load_all(root):
        if data.get('status') != 'measuring':
            continue
        record = latest_change_decision(root, data['branch'])
        since = instant(data.get('measuring_since'))
        if not record or (since and instant(record['created_at']) < since):
            continue
        decision = 'accepted' if record.get('status') == 'provisional' else record.get('status')
        if decision not in DECISIONS:
            continue
        run = (record.get('run_id') or '')[-8:]
        decide(root, data['branch'], decision, run=run or None, note=record.get('reason') or '')
        adopted.append(f'{data["branch"]} {decision}')
    return adopted


def latest_analysis(root):
    """The analysis recorded last, with its run's ID and score, from isuscope's index."""
    rows = isuscope_sql.rows(root, """
        SELECT a.id, a.body, r.id AS run_id, r.score, r.finished_at FROM run_analyses a JOIN runs r ON r.id = a.run_id
        ORDER BY a.created_at DESC, a.id DESC LIMIT 1""")
    return rows[0] if rows else None


def after_analysis(root):
    """After main records an analysis: the decisions reach the proposals, and once nothing measured waits
    for one, improve hears of the analysis (`main analyzed`) without main writing it again."""
    lines = []
    try:
        return after_analysis_reading(root, lines)
    except isuscope_sql.Unavailable as error:
        lines.append(f'isuscopeの記録を読めなかった（{error}）。採否は`python3 scripts/proposal.py decide`で投稿へ返す。')
        return '\n'.join(lines)


def after_analysis_reading(root, lines):
    if adopted := adopt_decisions(root):
        lines.append('isuscopeの採否を投稿へ写した: ' + ', '.join(adopted))
    analysis = latest_analysis(root)
    # main appends the detailed analysis of a run while the next benchmark runs: the proposals that
    # benchmark measures started after the analyzed run ended and do not hold this analysis back.
    ended = instant(analysis.get('finished_at') or '') if analysis else None
    owed = [data['branch'] for data in load_all(root) if data.get('status') == 'measuring'
            and (ended is None or (since := instant(data.get('measuring_since'))) is None or since <= ended)]
    if owed:
        lines.append(f'まだ採否の無い測定中の投稿: {", ".join(owed)}（`python3 scripts/proposal.py next RUN ... '
                     f'--decide {owed[0]}=<採否>`で記録する）。')
        return '\n'.join(lines)
    # The short analysis `next` recorded with the decisions is not improve's material; the detailed one is.
    if not analysis or analysis['id'] in quick_ids(root):
        return '\n'.join(lines)
    state = state_dir(root) / 'analyzed.json'
    told = read_json(state) or {}
    if told.get('analysis') != analysis['id']:
        body = ' '.join((analysis['body'] or '').split())
        run = analysis['run_id'][-8:]
        progress_emit(root, 'main', 'analyzed', f'{run} score {analysis["score"]}: {body[:300]}')
        write_json(state, {'analysis': analysis['id'], 'run': analysis['run_id']})
        lines.append(f'improveへ`main analyzed`（run {run}の分析の冒頭）を出した。')
    return '\n'.join(lines)


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
    """The commits of a proposal that main merged but has not decided, newest first. One being measured
    now is left out: its decision comes after the run."""
    if data.get('status') == 'measuring':
        return []
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
            *([f'- 確かめたこと: {data["verified"]}'] if data.get('verified') else []),
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
        parts.append(f'取り込んだが採否を記録していない投稿: {", ".join(waiting)}。測ったrunの'
                     '`python3 scripts/proposal.py next RUN ... --decide <branch>=<採否>`で記録する'
                     '（runが無い判断は`python3 scripts/proposal.py decide <branch> ...`）。')
    return '\n\n'.join(parts)


def drop_dependents(root, branch, reason):
    """The waiting proposals built on one that will not be taken leave the queue with it."""
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
    progress(root, decision, f'{branch} {target[:8]} run {run or "-"}: {(note or "")[:200]}')
    if entry is not data:
        return (f'{branch}の{target[:8]}（{base}へ取り込んだcommit）を{decision}にした。'
                f'今の投稿{data["commit"][:8]}はその後に出たもので、判断は付けていない。')
    lines = [f'{branch}（{target[:8]}）を{decision}にした。' + ('' if run else '（ベンチにかけずに決めた）')]
    if decision != 'accepted' and (dropped := drop_dependents(root, branch, decision)):
        lines.append(f'積んでいた投稿もキューから外した: {", ".join(dropped)}')
    return '\n'.join(lines)


def hold(root, branch, until, note=None):
    data = load(root, branch)
    if data:
        data = refresh(root, data)
    if data and (data.get('status') in ('merged', 'measuring')
                 or is_ancestor(root, data['commit'], main_session.main_branch(root))):
        raise Refused(f'{branch}はmainへ取り込み済みなので保留にできない。'
                      'コードを含んだまま測定対象から外さず、採否を決めて必要ならrevertする。')
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


STATUSES = ('accepted', 'provisional', 'rejected', 'deferred')
VERDICTS = ('supported', 'rejected', 'inconclusive', 'skipped')


def short_id(run_id):
    return (run_id or '')[-8:]


def quick_path(root):
    """The short analyses `next` recorded with the decisions; the detailed one follows during the next benchmark."""
    return state_dir(root) / 'quick-analyses.json'


def quick_ids(root):
    return read_json(quick_path(root)) or {}


def find_run(root, run):
    """A finished run by its ID or short ID, from isuscope's index."""
    if not re.fullmatch(r'[0-9a-f-]{4,36}', run or ''):
        raise Refused(f'runはIDか短縮ID（briefの先頭の8文字）で指定する: {run}')
    rows = isuscope_sql.rows(root, f"""
        SELECT id, state, passed, score, commit_hash, started_at, finished_at FROM runs
        WHERE id = '{run}' OR id LIKE '%{run}' ORDER BY started_at DESC""")
    if len(rows) != 1:
        raise Refused(f'run {run}が{"見つからない" if not rows else "一意に決まらない"}（`scripts/runs.sh list`で確かめる）。')
    if rows[0]['state'] in ('running', 'pending') or not rows[0]['finished_at']:
        raise Refused(f'run {run}はまだ終わっていない（state {rows[0]["state"]}）。')
    if not rows[0]['commit_hash']:
        raise Refused(f'run {run}のcommitが記録されていない。測った投稿を決められないので、'
                      '`isuscope analyze`と`proposal.py decide`で記録する。')
    return rows[0]


def measured_in(root, items, commit, run):
    """The proposals a run measured, with the commit each decision belongs to: merged into the commit the run
    deployed and not decided before it. Those already decided on this run come too, so `next` can run again."""
    found = []
    for data in items:
        decision = data.get('decision') or {}
        if data.get('status') in DECISIONS and decision.get('run') == short_id(run):
            found.append((data, decision.get('commit') or data['commit'], True))
            continue
        for entry in open_entries(data):
            if entry.get('status') in ('merged', 'measuring') and is_ancestor(root, entry['commit'], commit):
                found.append((data, entry['commit'], False))
                break
    return found


def run_analysis_ids(root, run_id):
    return {row['id'] for row in isuscope_sql.rows(root, f"SELECT id FROM run_analyses WHERE run_id = '{run_id}'")}


def base_of(root, run_id):
    """The run's comparison base: `run --base`, `isuscope base`, else what its analyses were written against."""
    rows = isuscope_sql.rows(root, f"""
        SELECT base_run_id FROM run_bases WHERE run_id = '{run_id}'
        UNION ALL SELECT base_run_id FROM (SELECT base_run_id FROM run_analyses
                                           WHERE run_id = '{run_id}' AND base_run_id IS NOT NULL
                                           ORDER BY created_at DESC LIMIT 1)""")
    return next((row['base_run_id'] for row in rows if row['base_run_id']), None)


def analyses_owed(root, but=None):
    """Runs whose only analyses are the short ones `next` recorded: their detailed analysis is still owed.
    A skipped run (the spread, a run that tested nothing) needs no more than its one line."""
    quick = quick_ids(root)
    runs = sorted({run for run in quick.values() if re.fullmatch(r'[0-9a-f-]{36}', run) and run != but})
    if not runs:
        return []
    listed = "', '".join(runs)
    rows = isuscope_sql.rows(root, f"""
        SELECT a.id, a.run_id, a.verdict FROM run_analyses a JOIN runs r ON r.id = a.run_id
        WHERE a.run_id IN ('{listed}') ORDER BY r.started_at, a.created_at""")
    by_run = {}
    for row in rows:
        by_run.setdefault(row['run_id'], []).append(row)
    return [run for run, analyses in by_run.items()
            if all(row['id'] in quick for row in analyses) and any(row['verdict'] != 'skipped' for row in analyses)]


def merge_that_brought(root, commit):
    """The merge on main's branch (first parent) that brought `commit` in."""
    log = git('log', '--first-parent', '--merges', '--format=%H %P', '-n', '500', 'HEAD', cwd=root).stdout
    for line in log.splitlines():
        merge, first, *others = line.split()
        if any(is_ancestor(root, commit, other) for other in others) and not is_ancestor(root, commit, first):
            return merge
    return None


def already_reverted(root, merge):
    return bool(git('log', '--first-parent', '--format=%H', '--fixed-strings',
                    f'--grep=This reverts commit {merge}', 'HEAD', cwd=root).stdout.strip())


def parse_decisions(pairs):
    """`--decide BRANCH=STATUS[:NOTE]` (git refuses `:` in a branch name) into {branch: (status, note)}."""
    decisions = {}
    for pair in pairs or []:
        branch, sep, rest = pair.partition('=')
        status, _, note = rest.partition(':')
        if not sep or not branch or status not in STATUSES:
            raise Refused(f'--decideは<branch>=<{"|".join(STATUSES)}>[:<理由>]で書く: {pair}')
        decisions[branch] = (status, note.strip())
    return decisions


def advance(root, run, verdict=None, note='', decide_pairs=(), merge=(), requeue_rest=False, revisit=None):
    """From a finished run to the next benchmark, in an order that keeps the code and the proposals in step:
    record the decisions in isuscope, copy them onto the proposals (which takes the proposals built on a
    rejected one out of the queue), revert what was not taken, check the next proposals again, and merge them.
    Every step looks at what is already done, so after a failure the same command continues."""
    row = find_run(root, run)
    run_id, short, passed = row['id'], short_id(row['id']), row['passed'] == 1
    decisions = parse_decisions(decide_pairs)
    note = (note or '').strip()
    base = main_session.main_branch(root)
    items = [refresh(root, data, base) for data in load_all(root)]
    by_branch = {data['branch']: data for data in items}
    measured = measured_in(root, items, row['commit_hash'], run_id)
    names = {data['branch'] for data, _, _ in measured}
    open_ones = [(data, commit) for data, commit, done in measured if not done]
    # Checked before anything is recorded: a refusal here leaves everything as it was.
    if passed and verdict not in VERDICTS:
        raise Refused(f'PASSしたrun {short}には、仮説の判定（{"|".join(VERDICTS)}）を付ける。')
    if not passed and verdict:
        raise Refused(f'run {short}はPASSしていないので、isuscopeへ判定を書けない。--verdictを外し、'
                      '投稿は--decide <branch>=rejected|deferredか--requeueで扱う。')
    # The note is each decision's reason unless the decision brings its own; a PASS run's verdict needs one too.
    if not note and not all([own for _, own in decisions.values()] or [not passed]):
        raise Refused('--noteに採否の決め手を短く書く（投稿の採否の理由にもなる）。')
    if requeue_rest and passed:
        raise Refused('PASSしたrunで測った投稿は、そのrunで採否を決める（測り直さない）。')
    stray = [b for b in decisions if b in by_branch and b not in names]
    if stray:
        raise Refused(f'run {short}で測っていない投稿: {", ".join(stray)}（run {short}のcommitに入っていない）。')
    if not passed and any(status in ('accepted', 'provisional') for status, _ in decisions.values()):
        raise Refused(f'PASSしていないrun {short}では採用しない。')
    missing = [data['branch'] for data, _ in open_ones if data['branch'] not in decisions]
    if missing and not requeue_rest:
        raise Refused(f'run {short}で測った投稿の採否が無い: {", ".join(missing)}。--decide <branch>=<採否>で全部に付ける'
                      + ('。' if passed else '（測り直すなら--requeue）。'))
    for data, commit in open_ones:
        if decisions.get(data['branch'], ('',))[0] in ('accepted', 'provisional') and not is_ancestor(root, commit, base):
            raise Refused(f'{data["branch"]}の{commit[:8]}は{base}に入っていない。採用は取り込んで測ったcommitにだけ付ける。')
    for branch in merge:
        if branch not in by_branch:
            raise Refused(f'{branch}の投稿は無い。`python3 scripts/proposal.py list`で確かめる。')

    lines = []
    # 1. isuscope: the run's verdict with each decision, as the short analysis main writes before the next
    # benchmark. A proposal an earlier `next` decided on this run is recorded there already.
    decided_before = {data['branch'] for data, _, done in measured if done}
    changes = [(change_id(branch) if branch in by_branch else branch, status, own or note)
               for branch, (status, own) in decisions.items() if branch not in decided_before]
    if passed and (changes or not decisions):
        before = run_analysis_ids(root, run_id)
        for change, status, body in changes or [(None, None, note)]:
            extra = ['--change', change, '--decision', status] if change else []
            if change and revisit and status == 'provisional':
                extra += [f'--revisit={revisit}']
            # isuscope takes text that starts with '-' (`-2%、…`) only as --option=value.
            done = subprocess.run(['isuscope', 'analyze', run_id, verdict, f'--analysis={body}', *extra], cwd=root,
                                  capture_output=True, text=True)
            if done.returncode:
                raise Refused('isuscope analyzeが失敗した（ここまでに記録したものは残る。直して同じコマンドを打つ）:\n'
                              + (done.stderr.strip() or done.stdout.strip()) + ('\n' + '\n'.join(lines) if lines else ''))
        # These short analyses are not told to improve (`main analyzed`): the detailed one is.
        if added := run_analysis_ids(root, run_id) - before:
            write_json(quick_path(root), {**quick_ids(root), **{analysis: run_id for analysis in added}})
        lines.append(f'run {short}を記録した（isuscope、判定 {verdict}）'
                     + (': ' + ', '.join(f'{change} {status}' for change, status, _ in changes) if changes else '。'))
    # 2. The proposals: decisions (taking the waiting ones built on a rejected one with them), or back to the queue.
    dropped = []
    for data, commit in open_ones:
        branch = data['branch']
        if branch not in decisions:
            continue
        status, own = decisions[branch]
        lines += decide(root, branch, 'accepted' if status == 'provisional' else status, run=short,
                        note=own or note, commit=commit).splitlines()
        if status not in ('accepted', 'provisional'):
            dropped += [d for d in load_all(root) if branch in (d.get('depends_on') or []) and d.get('status') == 'deferred']
    rest = [data['branch'] for data, _ in open_ones
            if data['branch'] not in decisions and data.get('status') == 'measuring']
    if requeue_rest and rest:
        lines.append(requeue(root, rest))
    # 3. Revert what was not taken: the decided ones, and those built on them that main had merged already.
    undo = [(data['branch'], commit) for data, commit, _ in measured
            if (load(root, data['branch']) or {}).get('status') in ('rejected', 'deferred')]
    undo += [(d['branch'], d['commit']) for d in dropped if is_ancestor(root, d['commit'], 'HEAD')]
    merges = []
    for branch, commit in undo:
        if not is_ancestor(root, commit, 'HEAD'):
            continue
        found = merge_that_brought(root, commit)
        if not found:
            raise Refused(f'{branch}（{commit[:8]}）をmainのbranchへ入れたmergeが見つからない。戻すcommitを確かめて'
                          '`git revert`し、同じコマンドを打ち直す。\n' + '\n'.join(lines))
        if not already_reverted(root, found) and found not in [m for m, _ in merges]:
            merges.append((found, branch))
    # Newest first, so each revert applies on the code it changed.
    order = git('log', '--first-parent', '--format=%H', '-n', '500', 'HEAD', cwd=root).stdout.split()
    for found, branch in sorted(merges, key=lambda item: order.index(item[0]) if item[0] in order else 0):
        reverted = git('revert', '--no-edit', '-m', '1', found, cwd=root)
        if reverted.returncode:
            git('revert', '--abort', cwd=root)
            raise Refused(f'{branch}のmerge {found[:8]}を戻せない（衝突）。`git revert -m 1 {found}`で戻して解消し、'
                          f'同じコマンドを打ち直す:\n{reverted.stdout.strip() or reverted.stderr.strip()}\n' + '\n'.join(lines))
        lines.append(f'{branch}を戻した（merge {found[:8]}をrevert）。')
    # 4. The next proposals, checked again now that the decisions may have taken them out, then merged.
    for branch in merge:
        data = refresh(root, load(root, branch), base)
        status = data.get('status')
        if status == 'merged':
            lines.append(f'{branch}はもう取り込んである（次のベンチで測る）。')
            continue
        if status not in ('submitted', 'delivered'):
            reason = {'needs-sync': '衝突している（`python3 scripts/proposal.py sync`で解消する）',
                      'held': '保留にしてある（`unhold`で戻す）', 'measuring': '測定中'}.get(
                status, f'{status}（{(data.get("decision") or {}).get("note") or ""}）')
            raise Refused(f'{branch}は取り込めない: {reason}。\n' + '\n'.join(lines))
        tip = git('rev-parse', '--verify', '-q', f'refs/heads/{branch}', cwd=root).stdout.strip()
        if tip != data['commit']:
            raise Refused(f'{branch}は投稿の後にbranchが動いた（{tip[:8] or "消えた"}）。投稿の`commit`と見比べる。\n'
                          + '\n'.join(lines))
        merged = git('merge', '--no-ff', '--no-edit', branch, cwd=root)
        if merged.returncode:
            git('merge', '--abort', cwd=root)
            raise Refused(f'{branch}を取り込めない（衝突）: {merged.stdout.strip() or merged.stderr.strip()}\n'
                          + '\n'.join(lines))
        refresh(root, load(root, branch), base)
        lines.append(f'{branch}を取り込んだ（次のベンチで測る）。')
    # 5. What comes next.
    if passed and any(status in ('accepted', 'provisional') for status, _ in decisions.values()):
        next_base = short
    else:
        next_base = short_id(base_of(root, run_id) or '') or (short if passed else '')
    bench = f'make bench BASE={next_base}' if next_base else 'make bench'
    lines.append(f'次: `{bench}`をrun_in_backgroundで起動する。'
                 + (f'起動したら最初に、run {short}の詳しい分析を`isuscope analyze {short} {verdict} --analysis "..."`で追記する'
                    '（improveへはこれが`main analyzed`で届く）。' if passed and verdict != 'skipped' else ''))
    if owed := analyses_owed(root, but=run_id):
        lines.append('詳しい分析がまだのrun: ' + ', '.join(short_id(r) for r in owed) + '（採否だけ記録してある）。')
    return '\n'.join(lines)


def withdraw(root, branch):
    data = load(root, branch)
    if not data:
        raise Refused(f'{branch}の投稿は無い。')
    data.update(status='withdrawn', withdrawn_at=now())
    save(root, data)
    progress(root, 'withdrawn', branch)
    dropped = drop_dependents(root, branch, 'withdrawn')
    return f'{branch}の投稿を取り下げた。' + (f'積んでいた投稿もキューから外した: {", ".join(dropped)}' if dropped else '')


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
    """Merge main's branch into each waiting proposal, in a worktree of main's own; stop at a conflict.
    One at a time: the hook starts it in the background when a benchmark starts, and main may run it too."""
    lock_path = Path(root) / '.local/proposal-sync.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return '別のsyncが動いている（ベンチの起動時にhookが始めたもの）。結果は`.local/proposal-sync.log`。'
        return sync_locked(root, branches)


def sync_locked(root, branches=None):
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
            lines.append(f'- {branch}: 投稿の後にbranchが動いた（{tip[:8] or "消えた"}）。投稿の`commit`と見比べて扱いを決める。')
            continue
        if is_ancestor(root, base, data['commit']):
            lines.append(f'- {branch}: 最新')
            continue
        if held_by.get(branch) and Path(held_by[branch]).resolve() != work.resolve():
            lines.append(f'- {branch}: {held_by[branch]}でcheckoutされている（そのworktreeを変えないので、外れるまで同期しない）。')
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
                         '`python3 scripts/proposal.py sync`。処理の意味がぶつかるときも両方の意図を残して解く。'
                         '解けなければ`git merge --abort`して棄却する。')
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


def wait(root, timeout=None, interval=WAIT_INTERVAL_SECONDS):
    """Return once the queue has room for improve's next proposal, or when main freezes. It runs in the
    background, so by default it waits as long as it takes."""
    deadline = None if timeout is None else time.time() + timeout
    while True:
        if frozen(root):
            raise Refused('mainが終了前の固定（freeze）に入った。新しい変更は投稿しない。', FROZEN_EXIT)
        waiting = queue_count(root)
        if waiting <= IMPROVE_ROOM:
            return f'キューの待ちは{waiting}件。投稿できる。'
        if deadline is not None and time.time() >= deadline:
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
    return {'waiting': queue_count(root, items), 'room': IMPROVE_ROOM, 'frozen': frozen(root), 'working': working_now(root),
            'items': [{**{key: data.get(key) for key in fields}, 'group': GROUPS[data['status']],
                       'author': (data.get('author') or {}).get('role') or 'worktree',
                       'agent': (data.get('author') or {}).get('agent') or ''} for data in shown]}


def describe(data):
    lines = [f'{data["branch"]}  {data.get("status")}  {data["commit"][:8]}  投稿 {age(data.get("submitted_at"))}',
             f'  仮説: {data.get("hypothesis")}', f'  期待する観測: {data.get("expect")}',
             *([f'  確かめたこと: {data["verified"]}'] if data.get('verified') else [])]
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
        lines.append('  mainへ取り込み済み。採否はまだ出ていない。')
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


TEXT_OPTIONS = ('--note', '--hypothesis', '--expect', '--verified', '--until', '--revisit')


def with_text_values(argv):
    """`--note "-2%、…"` as `--note=-2%、…`: free text may start with '-', which argparse before Python 3.14
    reads as the next option, leaving --note without its value."""
    joined, rest = [], list(argv)
    while rest:
        arg = rest.pop(0)
        joined.append(f'{arg}={rest.pop(0)}' if arg in TEXT_OPTIONS and rest else arg)
    return joined


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('submit')
    p.add_argument('--hypothesis', required=True, help='何を変えて、なぜ速くなる（点が上がる）と考えるか')
    p.add_argument('--expect', required=True, help='期待する観測値（route、SQL、CPUなど、briefとqueryで確かめられるもの）')
    p.add_argument('--verified', default='', help='ローカルで確かめたことがあれば')
    p.add_argument('--based-on', help='根拠にしたrunの短縮ID（mainが前提の古さを確かめる）')
    p.add_argument('--note')
    p = sub.add_parser('list')
    p.add_argument('--all', action='store_true')
    p = sub.add_parser('show')
    p.add_argument('branch', nargs='?')
    p = sub.add_parser('wait')
    p.add_argument('--timeout', type=int, help='この秒数で諦める（既定は空きが出るまで待つ）')
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
    sub.add_parser('adopt')
    p = sub.add_parser('next', help='runの採否を記録し、次の投稿を取り込む（make benchの前）')
    p.add_argument('run', help='終わったrunのIDか短縮ID')
    p.add_argument('--verdict', choices=VERDICTS, help='仮説の判定（PASSしたrunでは必須）')
    p.add_argument('--note', default='', help='採否の決め手（短く。投稿の採否の理由にもなる）')
    p.add_argument('--decide', action='append', default=[], metavar='BRANCH=STATUS[:NOTE]',
                   help=f'測った投稿の採否（{"|".join(STATUSES)}）。投稿でないchange ID（wait-...）も書ける')
    p.add_argument('--revisit', help='provisionalの再評価条件')
    p.add_argument('--merge', action='append', default=[], metavar='BRANCH', help='次のベンチで測る投稿')
    p.add_argument('--requeue', action='store_true', help='PASSしなかったrunで測った投稿を、待ちへ戻して測り直す')
    p = sub.add_parser('freeze')
    p.add_argument('--off', action='store_true')
    p.add_argument('--note')
    p = sub.add_parser('withdraw')
    p.add_argument('branch', nargs='?')
    p = sub.add_parser('working')
    p.add_argument('what', nargs='?', help='何をしているか（狙う制約と変更）')
    p.add_argument('--done', action='store_true')
    sub.add_parser('hypothesis')
    args = parser.parse_args(with_text_values(sys.argv[1:]))
    root = main_session.root_repo()
    _, session = main_session.current_session()
    try:
        if args.action in ('pending', 'sync', 'hold', 'unhold', 'requeue', 'decide', 'freeze', 'next') \
                and main_session.is_main(root, session) is False:
            raise Refused('これはmain（/mainで宣言したセッション）の操作。')
        if args.action == 'submit':
            print(submit(root, args.hypothesis, args.expect, args.verified, args.note, based_on=args.based_on))
        elif args.action == 'next':
            print(advance(root, args.run, args.verdict, args.note, args.decide, args.merge, args.requeue, args.revisit))
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
        elif args.action == 'working':
            print(working(root, args.what, args.done))
        elif args.action == 'adopt':
            print(after_analysis(root) or 'measuring中の投稿も、新しい分析も無い。')
        elif args.action == 'hypothesis':
            print(run_hypothesis(root))
        elif args.action == 'withdraw':
            print(withdraw(root, args.branch or own_branch(root)))
        elif args.action == 'show':
            branch = args.branch or own_branch(root)
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
