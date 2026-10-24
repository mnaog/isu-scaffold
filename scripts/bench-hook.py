#!/usr/bin/env python3
"""What follows a benchmark and `isuscope analyze`, for the benchmark hook.

The hook (scripts/hook.sh post, built from tools/isu-hook) recognises the Bash call and runs this
with what it found; this prints the context for the session, or nothing.

  bench-hook.py started --session ID [--unbased]   a benchmark started in the background
  bench-hook.py analyzed --session ID              `isuscope analyze` ran

- started: asks the session to review the scenario map (docs/benchmark-scenario.md) against its
  hypothesis. It also hands main the worktree proposals (scripts/proposal.py) it has not seen at
  their current commit and that merge into main's branch cleanly, so main picks and builds the next
  candidate while the benchmark runs. While a main is declared (/main) only main gets them. The
  proposals main merged for this benchmark leave the queue as measuring. It prints nothing while
  there is neither.
- started --unbased (a benchmark main started without `--base`, or `BASE=` for make bench): a
  reminder to choose a comparison base while it runs and set it with `isuscope base running <run>`,
  with the last run an accepted change was decided on and the previous finished run as candidates.
  brief shows no differences without a base. There is no reminder before the first finished run.
- started also drops the state-log caches earlier isuscope versions left and keeps isuscope-data
  out of Spotlight and Time Machine (scripts/isuscope-prune.py).
- analyzed: copies the decisions main recorded with `isuscope analyze` (`--change <branch>
  --decision ...`) onto the proposals being measured, and once none waits for a decision, writes
  `main analyzed` for improve from that analysis (scripts/proposal.py after_analysis), so main
  writes each decision and each analysis once.

Internal errors print nothing on stdout, like the hook.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
SCENARIO = 'docs/benchmark-scenario.md'
SCENARIO_HINT = (f'ベンチの完了を待つ間に、`{SCENARIO}`（ベンチの見取り図）を読み返す。'
                 '今回の仮説と次の候補が、得点の仕組み・負荷の流れ・失敗の条件と合っているか確かめる。'
                 'ベンチの仕様（得点・FAIL条件・シナリオの流れ・応答へのベンチの反応）について、'
                 '観測で確かめた・食い違った記述だけを該当する節の中で直す（推測を事実へ、出典にrun ID）。'
                 'runのスコア、改善の効果、負荷や制約の推移は書かない（isuscope analyzeへ）。')
BASE_HINT = ('このベンチの比較の基準runが未指定。ベンチ中に決め、`isuscope base running <run>`で指定する'
             '（briefは基準runとの差を出す。未指定なら差を出さない）。')
spec = importlib.util.spec_from_file_location('proposal', Path(__file__).with_name('proposal.py'))
proposal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proposal)
spec = importlib.util.spec_from_file_location('isuscope_prune', Path(__file__).with_name('isuscope-prune.py'))
isuscope_prune = importlib.util.module_from_spec(spec)
spec.loader.exec_module(isuscope_prune)
spec = importlib.util.spec_from_file_location('isuscope_sql', Path(__file__).with_name('isuscope_sql.py'))
isuscope_sql = importlib.util.module_from_spec(spec)
spec.loader.exec_module(isuscope_sql)


def main_worktree(root):
    try:
        common = subprocess.check_output(['git', '-C', str(root), 'rev-parse', '--path-format=absolute',
                                          '--git-common-dir'], text=True, stderr=subprocess.DEVNULL).strip()
        return Path(common).parent
    except (OSError, subprocess.CalledProcessError):
        return root


def analyzed(root, session=''):
    """After `isuscope analyze` by main: decisions onto the proposals, and `main analyzed` for improve."""
    repo = main_worktree(root)
    if proposal.main_session.is_main(repo, session) is False:
        return None
    return proposal.after_analysis(repo) or None


def proposal_context(root, session='', now=None):
    """Worktree proposals for main: none for a session that is not main, or without a session id."""
    if not session:
        return None
    repo = main_worktree(root)
    if proposal.main_session.is_main(repo, session) is False:
        return None
    return proposal.deliver(repo, session, now)


def waiting_context(root, session='', now=None):
    """What to do while a background benchmark runs: the scenario map and new proposals."""
    parts = [SCENARIO_HINT] if (root / SCENARIO).is_file() else []
    if proposals := proposal_context(root, session, now):
        parts.append(proposals)
    return '\n\n'.join(parts) or None


def start_sync(repo):
    """Bring the waiting proposals up to main's branch while the benchmark runs, in the background, then
    build and test what main may deploy next (scripts/prebuild.py). The sync stops at the first conflict
    and leaves it in .local/proposal-sync; the output says which."""
    if os.environ.get('PROPOSAL_AUTO_SYNC') == '0':  # tests that drive sync step by step
        return
    local = repo / '.local'
    here = Path(__file__).resolve().parent
    try:
        local.mkdir(parents=True, exist_ok=True)
        # One background shell: the prebuild needs the synced branches, so it starts when the sync ends.
        subprocess.Popen(['sh', '-c', '"$0" "$1" sync >"$2" 2>&1; "$0" "$3" >"$4" 2>&1',
                          sys.executable, str(here / 'proposal.py'), str(local / 'proposal-sync.log'),
                          str(here / 'prebuild.py'), str(local / 'prebuild.log')],
                         cwd=repo, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        pass  # main can still run them by hand


def base_reminder(repo):
    """Ask main to choose the comparison base while the benchmark runs; None before the first finished run."""
    try:
        previous = isuscope_sql.rows(repo, (
            "SELECT substr(r.id, -8) AS id, r.score, (SELECT verdict FROM run_analyses a WHERE a.run_id = r.id "
            "ORDER BY a.created_at DESC LIMIT 1) AS verdict FROM runs r "
            "WHERE r.state IN ('complete', 'degraded', 'failed') ORDER BY r.started_at DESC LIMIT 1"))
        accepted = isuscope_sql.rows(repo, (
            "SELECT substr(r.id, -8) AS id, r.score FROM runs r JOIN change_decision_runs c ON c.run_id = r.id "
            "JOIN change_decisions d ON d.id = c.decision_id WHERE d.status = 'accepted' "
            "ORDER BY r.started_at DESC LIMIT 1"))
    except (isuscope_sql.Unavailable, ValueError, KeyError):
        return None
    if not previous:
        return None
    last = previous[0]
    candidates = []
    if accepted and accepted[0]['id'] != last['id']:
        candidates.append(f"最後に採用 {accepted[0]['id']}（{accepted[0]['score']}）")
    candidates.append(f"直前 {last['id']}（{last['score']}、{last['verdict'] or '分析なし'}"
                      f"{'、最後に採用' if accepted and accepted[0]['id'] == last['id'] else ''}）")
    return f"{BASE_HINT}候補: {'、'.join(candidates)}。"


def started(root, session, based=True):
    """A benchmark started in the background: the merged proposals are measured now, then the waiting context."""
    repo = main_worktree(root)
    try:
        isuscope_prune.exclude_from_backups(repo)
        isuscope_prune.prune(repo)
    except OSError:
        pass  # the caches are only disk space; the benchmark and its context matter more
    requeued = []
    if proposal.main_session.is_main(repo, session) is not False:
        # A PASS run blocks the next benchmark until it is analyzed, so a proposal still measuring here
        # was on a run that failed or was cut short: it goes back to be measured on this one.
        try:
            requeued = proposal.requeue(repo).removeprefix('キューへ戻した: ').split(', ')
        except proposal.Refused:
            requeued = []
        measuring = proposal.start_measuring(repo)
        start_sync(repo)
        reminder = None if based else base_reminder(repo)
    else:
        measuring, reminder = [], None
    context = waiting_context(root, session)
    if reminder:
        context = '\n\n'.join(part for part in (reminder, context) if part)
    if measuring:
        named = ', '.join(f'{branch}（--change {proposal.change_id(branch)}）' for branch in measuring)
        again = f'前のrunで採否の付かなかった{", ".join(requeued)}も、もう一度このベンチで測る。' if requeued else ''
        note = f'このベンチで測る投稿: {named}（キューから外し、測定中にした）。{again}'
        context = '\n\n'.join(part for part in (note, context) if part)
    return context


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('event', choices=['started', 'analyzed'])
    parser.add_argument('--session', default='')
    parser.add_argument('--unbased', action='store_true', help='the benchmark named no comparison base')
    args = parser.parse_args()
    try:
        root = Path(os.environ.get('TOOL_GUARD_ROOT') or ROOT)
        if args.event == 'started':
            context = started(root, args.session, not args.unbased)
        else:
            context = analyzed(root, args.session)
        if context:
            print(context)
    except Exception as error:  # a broken hook must not stop every tool call
        print(f'bench-hook: skipped ({error})', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
