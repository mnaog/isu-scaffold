#!/usr/bin/env python3
"""Claude Code hooks around a benchmark: start it in the background, and read the scouts meanwhile.

.claude/settings.json runs this next to scripts/tool-guard.py, whose shell parsing it reuses to
find a benchmark (isuscope run / survey-run, make first-bench, scripts/first-bench.sh) in a
Bash call. Heredoc bodies and `bash -n` syntax checks do not count as starting one.

- `--pre` (PreToolUse) refuses a benchmark started in the foreground, so the session can read
  the scouts' reports and build the next candidate while it runs.
- `--post` (PostToolUse) hands the latest scout reports (.local/scout/reports.md in the main
  worktree), with their age, to the session right after a benchmark starts in the background.
  It prints nothing while no scout has posted.

Codex has no background shell and does not run this; it reads the reports before a benchmark.
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
SCOUT_HINT = ('これは並列で動かしている調査エージェント（scout）による報告です。'
              '内容が古くなっている可能性もありますが（各報告に投稿からの経過分を添えています）、'
              'それぞれの視点を参考にして、次回の改善のための参考として確認してください。')

spec = importlib.util.spec_from_file_location('tool_guard', Path(__file__).with_name('tool-guard.py'))
tool_guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool_guard)


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


def scout_context(root, now=None):
    """The latest scout reports with their age, or None while no scout has posted."""
    try:
        reports = (main_worktree(root) / '.local/scout/reports.md').read_text()
    except OSError:
        return None
    if '投稿: ' not in reports:
        return None
    now = time.time() if now is None else now

    def age(match):
        try:
            posted = calendar.timegm(time.strptime(match.group(1), '%Y-%m-%dT%H:%M:%SZ'))
        except ValueError:
            return match.group(0)
        return f'{match.group(0)}（{max(0, int(now - posted) // 60)}分前）'
    return SCOUT_HINT + '\n\n' + re.sub(r'投稿: (\S+Z)', age, reports).strip()


def main():
    try:
        payload = json.load(sys.stdin)
        payload = payload if isinstance(payload, dict) else {}
        root = Path(os.environ.get('TOOL_GUARD_ROOT') or ROOT)
        benchmark, background = starts_benchmark(root, payload)
        if not benchmark:
            return 0
        if '--pre' in sys.argv and not background:
            output = {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                      'permissionDecisionReason': BACKGROUND_HINT}
        elif '--post' in sys.argv and background and (context := scout_context(root)):
            output = {'hookEventName': 'PostToolUse', 'additionalContext': context}
        else:
            return 0
        print(json.dumps({'hookSpecificOutput': output}, ensure_ascii=False))
    except Exception as error:  # a broken hook must not stop every tool call
        print(f'bench-hook: skipped ({error})', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
