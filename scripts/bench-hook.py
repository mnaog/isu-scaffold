#!/usr/bin/env python3
"""Claude Code hooks around a benchmark: start it in the background, and read meanwhile.

.claude/settings.json runs this next to scripts/tool-guard.py, whose shell parsing it reuses to
find a benchmark (isuscope run / survey-run, make first-bench, scripts/first-bench.sh) in a
Bash call. Heredoc bodies and `bash -n` syntax checks do not count as starting one.

- `--pre` (PreToolUse) refuses a benchmark started in the foreground, so the session can read
  and build the next candidate while it runs.
- `--post` (PostToolUse) asks the session, right after a benchmark starts in the background, to
  review the scenario map (docs/benchmark-scenario.md) against its hypothesis. It also hands main
  the worktree proposals (scripts/proposal.py) it has not seen at their current commit and that
  merge into main's branch cleanly, so main picks and builds the next candidate while the
  benchmark runs. While a main is declared (/main) only main gets them. The proposals main merged
  for this benchmark leave the queue as measuring. It prints nothing while there is neither.
- `--post` after `isuscope analyze` copies the decisions main recorded there (`--change <branch>
  --decision ...`) onto the proposals being measured, and once none waits for a decision, writes
  `main analyzed` for improve from that analysis (scripts/proposal.py after_analysis), so main
  writes each decision and each analysis once.
- When a benchmark starts, it also drops the state-log caches earlier isuscope versions left and keeps
  isuscope-data out of Spotlight and Time Machine (scripts/isuscope-prune.py).

Internal errors let the call through, like tool-guard.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
BENCH_ISUSCOPE = {'run', 'survey-run'}
BACKGROUND_HINT = ('ベンチ（isuscope run / survey-run / make first-bench）はBashのrun_in_background: trueで起動する。'
                   '完了の通知を待つ間に、次の候補を`make build`しておく。'
                   'sleepやpollingで待たない。')
SCENARIO = 'docs/benchmark-scenario.md'
SCENARIO_HINT = (f'ベンチの完了を待つ間に、`{SCENARIO}`（ベンチの見取り図）を読み返す。'
                 '今回の仮説と次の候補が、得点の仕組み・負荷の流れ・失敗の条件と合っているか確かめる。'
                 'ベンチの仕様（得点・FAIL条件・シナリオの流れ・応答へのベンチの反応）について、'
                 '観測で確かめた・食い違った記述だけを該当する節の中で直す（推測を事実へ、出典にrun ID）。'
                 'runのスコア、改善の効果、負荷や制約の推移は書かない（isuscope analyzeへ）。')
spec = importlib.util.spec_from_file_location('tool_guard', Path(__file__).with_name('tool-guard.py'))
tool_guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool_guard)
spec = importlib.util.spec_from_file_location('proposal', Path(__file__).with_name('proposal.py'))
proposal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proposal)
spec = importlib.util.spec_from_file_location('isuscope_prune', Path(__file__).with_name('isuscope-prune.py'))
isuscope_prune = importlib.util.module_from_spec(spec)
spec.loader.exec_module(isuscope_prune)


def is_benchmark(program, args):
    """isuscope run / survey-run, make first-bench, or scripts/first-bench.sh, but not their help."""
    options = [a for a in args if a.startswith('-')]
    if '-h' in options or '--help' in options:
        return False
    if program == 'isuscope':
        return bool(args) and args[0] in BENCH_ISUSCOPE
    if program == 'make':
        return 'first-bench' in [a for a in args if not a.startswith('-')]
    if program in tool_guard.SHELLS:
        if '-n' in options:
            return False  # a syntax check reads the script without running it
        program = os.path.basename(next((a for a in args if not a.startswith('-')), ''))
    return program == 'first-bench.sh'


class BenchmarkFinder(tool_guard.Guard):
    """tool-guard's parser, collecting benchmarks instead of enforcing its rules."""

    def __init__(self, root):
        super().__init__(root)
        self.lane = None
        self.drop_heredocs = True
        self.benchmarks = []
        self.analyses = []

    def seen(self, program, args):
        if is_benchmark(program, args):
            self.benchmarks.append(program)
        elif program == 'isuscope' and args[:1] == ['analyze'] and not {'-h', '--help'} & set(args):
            self.analyses.append(program)


def find(root, payload):
    """The benchmarks and analyses a Bash call runs, or None for any other call."""
    tool_input = payload.get('tool_input')
    if payload.get('tool_name') != 'Bash' or not isinstance(tool_input, dict):
        return None
    command = tool_guard.command_of(tool_input)
    if not isinstance(command, str):
        return None
    finder = BenchmarkFinder(root)
    try:
        finder.check_shell(command)
    except tool_guard.Denied:
        pass  # tool-guard decides on its own rules; only what runs matters here
    return finder


def starts_benchmark(root, payload):
    """True when this Bash call starts a benchmark, and whether it runs in the background."""
    finder = find(root, payload)
    if finder is None:
        return False, False
    return bool(finder.benchmarks), payload['tool_input'].get('run_in_background') is True


def analyzed(root, payload):
    """After `isuscope analyze` by main: decisions onto the proposals, and `main analyzed` for improve."""
    finder = find(root, payload)
    if finder is None or not finder.analyses:
        return None
    repo = main_worktree(root)
    if proposal.main_session.is_main(repo, str(payload.get('session_id') or '')) is False:
        return None
    return proposal.after_analysis(repo) or None


def main_worktree(root):
    try:
        common = subprocess.check_output(['git', '-C', str(root), 'rev-parse', '--path-format=absolute',
                                          '--git-common-dir'], text=True, stderr=subprocess.DEVNULL).strip()
        return Path(common).parent
    except (OSError, subprocess.CalledProcessError):
        return root


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
    """Bring the waiting proposals up to main's branch while the benchmark runs, in the background.
    It stops at the first conflict and leaves it in .local/proposal-sync; the output says which."""
    if os.environ.get('PROPOSAL_AUTO_SYNC') == '0':  # tests that drive sync step by step
        return
    log = repo / '.local/proposal-sync.log'
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('w') as out:
            subprocess.Popen([sys.executable, str(Path(__file__).with_name('proposal.py')), 'sync'], cwd=repo,
                             stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass  # main can still run it by hand


def started(root, session):
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
    else:
        measuring = []
    context = waiting_context(root, session)
    if measuring:
        named = ', '.join(f'{branch}（--change {proposal.change_id(branch)}）' for branch in measuring)
        again = f'前のrunで採否の付かなかった{", ".join(requeued)}も、もう一度このベンチで測る。' if requeued else ''
        note = f'このベンチで測る投稿: {named}（キューから外し、測定中にした）。{again}'
        context = '\n\n'.join(part for part in (note, context) if part)
    return context


def main():
    try:
        payload = json.load(sys.stdin)
        payload = payload if isinstance(payload, dict) else {}
        root = Path(os.environ.get('TOOL_GUARD_ROOT') or ROOT)
        benchmark, background = starts_benchmark(root, payload)
        if not benchmark:
            if '--post' in sys.argv and (context := analyzed(root, payload)):
                print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': context}},
                                 ensure_ascii=False))
            return 0
        if '--pre' in sys.argv and not background:
            output = {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                      'permissionDecisionReason': BACKGROUND_HINT}
        elif '--post' in sys.argv and background and (context := started(root, str(payload.get('session_id') or ''))):
            output = {'hookEventName': 'PostToolUse', 'additionalContext': context}
        else:
            return 0
        print(json.dumps({'hookSpecificOutput': output}, ensure_ascii=False))
    except Exception as error:  # a broken hook must not stop every tool call
        print(f'bench-hook: skipped ({error})', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
