#!/usr/bin/env python3
"""Claude Code hooks around a benchmark: start it in the background, and read the scouts meanwhile.

.claude/settings.json runs this next to scripts/tool-guard.py, whose shell parsing it reuses to
find a benchmark (isuscope run / survey-run, make first-bench, scripts/first-bench.sh) in a
Bash call. Heredoc bodies and `bash -n` syntax checks do not count as starting one.

- `--pre` (PreToolUse) refuses a benchmark started in the foreground, so the session can read
  the scouts' reports and build the next candidate while it runs.
- `--post` (PostToolUse) asks the session, right after a benchmark starts in the background, to
  review the scenario map (docs/benchmark-scenario.md) against its hypothesis, and hands it the
  scout reports (.local/scout/reports.md in the main worktree) with their age. Each report goes to
  a session once (.local/scout/delivered.json); a later benchmark gets only the reports posted
  since. It prints nothing while neither the map nor an undelivered report exists.
- `--emergency` (PostToolUse on every tool) hands main a report the person in worktree `human`
  posted with `scripts/scout/main.py post --emergency`, at main's next tool call instead of the
  next benchmark. The settings call it only while .local/scout/emergency.json exists.
  It also hands main the worktree proposals (scripts/proposal.py) it has not seen at their current
  commit and that merge into main's branch cleanly, so main picks and builds the next candidate
  while the benchmark runs. While a main is declared (/main) only main gets them. The proposals
  main merged for this benchmark leave the queue as measuring.

Internal errors let the call through, like tool-guard.
"""
import calendar
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
BENCH_ISUSCOPE = {'run', 'survey-run'}
BACKGROUND_HINT = ('ベンチ（isuscope run / survey-run / make first-bench）はBashのrun_in_background: trueで起動する。'
                   '完了の通知を待つ間に、scoutの報告があれば読み、次の候補を`make build`しておく。'
                   'sleepやpollingで待たない。')
SCENARIO = 'docs/benchmark-scenario.md'
SCENARIO_HINT = (f'ベンチの完了を待つ間に、`{SCENARIO}`（ベンチの見取り図）を読み返す。'
                 '今回の仮説と次の候補が、得点の仕組み・負荷の流れ・失敗の条件と合っているか確かめる。'
                 '観測と食い違う記述や新しく分かった事実があれば、文書を直す。')
SCOUT_HINT = ('これは並列で動かしている調査エージェント（scout）と、worktree `human`の人間（`## human`）による報告です。'
              '内容が古くなっている可能性もありますが（各報告に投稿からの経過分を添えています）、'
              'それぞれの視点を参考にして、次回の改善のための参考として確認してください。'
              '前回までのベンチで渡した報告は省いています（各scoutの最新の報告は`.local/scout/reports.md`）。')
EMERGENCY_HINT = ('人間（worktree `human`）からの緊急の報告です。今の作業を続ける前に読み、'
                  'すぐ反映するか、反映しない理由を人間へ伝えてください。')

spec = importlib.util.spec_from_file_location('tool_guard', Path(__file__).with_name('tool-guard.py'))
tool_guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool_guard)
spec = importlib.util.spec_from_file_location('proposal', Path(__file__).with_name('proposal.py'))
proposal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proposal)


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

    def seen(self, program, args):
        if is_benchmark(program, args):
            self.benchmarks.append(program)


def starts_benchmark(root, payload):
    """True when this Bash call starts a benchmark, and whether it runs in the background."""
    tool_input = payload.get('tool_input')
    if payload.get('tool_name') != 'Bash' or not isinstance(tool_input, dict):
        return False, False
    command = tool_guard.command_of(tool_input)
    if not isinstance(command, str):
        return False, False
    finder = BenchmarkFinder(root)
    try:
        finder.check_shell(command)
    except tool_guard.Denied:
        pass  # tool-guard decides on its own rules; only the benchmarks matter here
    return bool(finder.benchmarks), tool_input.get('run_in_background') is True


def main_worktree(root):
    try:
        common = subprocess.check_output(['git', '-C', str(root), 'rev-parse', '--path-format=absolute',
                                          '--git-common-dir'], text=True, stderr=subprocess.DEVNULL).strip()
        return Path(common).parent
    except (OSError, subprocess.CalledProcessError):
        return root


def undelivered(scouts, session, sections):
    """The sections this session has not been handed yet, recording them as handed."""
    path = scouts / 'delivered.json'
    try:
        delivered = json.loads(path.read_text())
    except (OSError, ValueError):
        delivered = {}
    seen = set(delivered.get(session, []))
    # A report is one scout's post, told apart by its time (reports.md keeps only the latest).
    fresh = [(key, text) for key, text in sections if key not in seen]
    if fresh:
        delivered[session] = sorted(seen | {key for key, _ in fresh})
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(delivered, ensure_ascii=False))
        tmp.replace(path)
    return [text for _, text in fresh]


def scout_context(root, session='', now=None):
    """The scout reports this session has not seen, with their age, or None while there are none."""
    scouts = main_worktree(root) / '.local/scout'
    try:
        reports = (scouts / 'reports.md').read_text()
    except OSError:
        return None
    sections = []
    for section in re.split(r'^(?=## )', reports, flags=re.M):
        posted = re.search(r'^## (\S+)\n+投稿: (\S+Z)', section, re.M)
        if posted:
            sections.append((f'{posted.group(1)}@{posted.group(2)}', section.strip()))
    fresh = undelivered(scouts, session, sections)
    if not fresh:
        return None
    now = time.time() if now is None else now

    def age(match):
        try:
            posted = calendar.timegm(time.strptime(match.group(1), '%Y-%m-%dT%H:%M:%SZ'))
        except ValueError:
            return match.group(0)
        return f'{match.group(0)}（{max(0, int(now - posted) // 60)}分前）'
    return SCOUT_HINT + '\n\n' + re.sub(r'投稿: (\S+Z)', age, '\n\n'.join(fresh))


def emergency_context(root, session='', now=None):
    """A report posted with --emergency, handed to main at its next tool call (once), or None.

    The marker sits in the main worktree, where main runs, so other sessions pass it by at once.
    """
    marker = Path(root) / '.local/scout/emergency.json'
    if not session or not marker.is_file():
        return None
    if proposal.main_session.is_main(main_worktree(root), session) is False:
        return None
    try:
        key = json.loads(marker.read_text())['key']
        reports = (marker.parent / 'reports.md').read_text()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    name = key.split('@', 1)[0]
    for section in re.split(r'^(?=## )', reports, flags=re.M):
        posted = re.search(r'^## (\S+)\n+投稿: (\S+Z)', section, re.M)
        if posted and f'{posted.group(1)}@{posted.group(2)}' == key and posted.group(1) == name:
            # Recorded as delivered like any report, so the next benchmark does not hand it again.
            fresh = undelivered(marker.parent, session, [(key, section.strip())])
            return EMERGENCY_HINT + '\n\n' + fresh[0] if fresh else None
    return None


def proposal_context(root, session='', now=None):
    """Worktree proposals for main: none for a session that is not main, or without a session id."""
    if not session:
        return None
    repo = main_worktree(root)
    if proposal.main_session.is_main(repo, session) is False:
        return None
    return proposal.deliver(repo, session, now)


def waiting_context(root, session='', now=None):
    """What to do while a background benchmark runs: the scenario map, new scout reports, new proposals."""
    parts = [SCENARIO_HINT] if (root / SCENARIO).is_file() else []
    if scouts := scout_context(root, session, now):
        parts.append(scouts)
    if proposals := proposal_context(root, session, now):
        parts.append(proposals)
    return '\n\n'.join(parts) or None


def started(root, session):
    """A benchmark started in the background: the merged proposals are measured now, then the waiting context."""
    repo = main_worktree(root)
    if proposal.main_session.is_main(repo, session) is not False:
        measuring = proposal.start_measuring(repo)
    else:
        measuring = []
    context = waiting_context(root, session)
    if measuring:
        note = (f'このベンチで測る投稿: {", ".join(measuring)}（キューから外し、測定中にした）。'
                'ベンチを待つ間に`python3 scripts/proposal.py sync`で待っている投稿をmainのbranchへ追従させる。')
        context = '\n\n'.join(part for part in (note, context) if part)
    return context


def main():
    try:
        payload = json.load(sys.stdin)
        payload = payload if isinstance(payload, dict) else {}
        root = Path(os.environ.get('TOOL_GUARD_ROOT') or ROOT)
        if '--emergency' in sys.argv:
            if context := emergency_context(root, str(payload.get('session_id') or '')):
                print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PostToolUse', 'additionalContext': context}},
                                 ensure_ascii=False))
            return 0
        benchmark, background = starts_benchmark(root, payload)
        if not benchmark:
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
