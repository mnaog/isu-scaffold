import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / 'scripts/bench-hook.py'


# Answers `isuscope sql` with the previous finished run and the last run an accepted change was
# decided on, or with no rows when FAKE_NO_RUNS is set.
FAKE_ISUSCOPE = """#!/usr/bin/env python3
import json, os, sys
query = sys.argv[-1]
if os.environ.get('FAKE_NO_RUNS'):
    rows = []
elif 'change_decisions' in query:
    rows = [['856309ef', 970776]]
else:
    rows = [['3bbbebf8', 958487, 'rejected']]
columns = ['id', 'score'] if 'change_decisions' in query else ['id', 'score', 'verdict']
print(json.dumps({'columns': columns, 'rows': rows}))
"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class BenchHookTest(unittest.TestCase):
    """Claude Code starts benchmarks in the background and reads the scenario map and proposals meanwhile."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def hook(self, tool_input, *args, tool='Bash', session='main', env=None):
        payload = {'tool_name': tool, 'tool_input': tool_input, 'cwd': str(self.root), 'session_id': session}
        env = {**{k: v for k, v in os.environ.items() if k != 'ISUCON_LANE'}, **(env or {})}
        env['TOOL_GUARD_ROOT'] = str(self.root)
        result = subprocess.run(['python3', str(HOOK), *args], input=json.dumps(payload), text=True,
                                capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        return json.loads(result.stdout)['hookSpecificOutput'] if result.stdout.strip() else None

    def test_benchmarks_start_only_in_the_background(self):
        for command in [
            'isuscope run --hypothesis "indexを足す"',
            'make first-bench',
            'cd /repo && isuscope run --repeat "再測定" --hypothesis x',
            'bash scripts/first-bench.sh',
            'isuscope lock -- isuscope run --hypothesis x',
            'bash -c "isuscope run --hypothesis x"',
        ]:
            with self.subTest(command=command):
                denied = self.hook({'command': command}, '--pre')
                self.assertEqual(denied['permissionDecision'], 'deny')
                self.assertIn('run_in_background', denied['permissionDecisionReason'])
                self.assertIsNone(self.hook({'command': command, 'run_in_background': True}, '--pre'))
        for command in ['isuscope run --help', 'isuscope brief latest', 'make build', 'echo "isuscope run"',
                        'bash -n scripts/first-bench.sh', 'git commit -m "isuscope run の結果"',
                        "python3 - <<'EOF'\nnotes = 1\nisuscope run --hypothesis x\nEOF"]:
            with self.subTest(command=command):
                self.assertIsNone(self.hook({'command': command}, '--pre'))
        self.assertIsNone(self.hook({'command': 'isuscope run'}, '--pre', tool='Monitor'))

    def test_only_a_background_benchmark_gets_the_waiting_context(self):
        started = {'command': 'isuscope run --hypothesis x', 'run_in_background': True}
        # No map and no proposal yet: nothing to add.
        self.assertIsNone(self.hook(started, '--post'))
        write(self.root / 'docs/benchmark-scenario.md', '# ベンチの見取り図\n')
        self.assertEqual(self.hook(started, '--post')['hookEventName'], 'PostToolUse')
        for tool_input in [{'command': 'isuscope run --hypothesis x'}, {'command': 'make build', 'run_in_background': True}]:
            with self.subTest(tool_input=tool_input):
                self.assertIsNone(self.hook(tool_input, '--post'))
        # tool-guard keeps its own rules (a benchmark node here); bench-hook only looks for benchmarks.
        self.assertIsNone(self.hook({'command': 'ssh bench && isuscope run', 'run_in_background': True}, '--pre'))

    def test_a_background_benchmark_asks_to_review_the_scenario_map(self):
        started = {'command': 'isuscope run --hypothesis x', 'run_in_background': True}
        write(self.root / 'docs/benchmark-scenario.md', '# ベンチの見取り図\n')
        output = self.hook(started, '--post')
        self.assertIn('docs/benchmark-scenario.md', output['additionalContext'])
        self.assertIsNone(self.hook({'command': 'isuscope run --hypothesis x'}, '--post'))

    def test_a_benchmark_without_a_base_asks_main_to_choose_one_meanwhile(self):
        bin_dir = self.root / 'bin'
        write(bin_dir / 'isuscope', FAKE_ISUSCOPE)
        (bin_dir / 'isuscope').chmod(0o755)
        env = {'PATH': f'{bin_dir}{os.pathsep}{os.environ["PATH"]}'}
        output = self.hook({'command': 'make bench', 'run_in_background': True}, '--post', env=env)
        context = output['additionalContext']
        self.assertIn('`isuscope base running <run>`', context)
        self.assertIn('最後に採用 856309ef（970776）', context)
        self.assertIn('直前 3bbbebf8（958487、rejected）', context)
        # A named base needs no reminder, and there is nothing else to say here.
        for command in ['make bench BASE=856309ef', 'scripts/bench.sh --base 856309ef',
                        'isuscope run --base=856309ef --hypothesis x']:
            with self.subTest(command=command):
                self.assertIsNone(self.hook({'command': command, 'run_in_background': True}, '--post', env=env))
        # Before the first finished run there is no base to choose.
        self.assertIsNone(self.hook({'command': 'make first-bench', 'run_in_background': True}, '--post',
                                    env={**env, 'FAKE_NO_RUNS': '1'}))

    def test_claude_settings_run_both_hooks(self):
        claude = json.loads((ROOT / '.claude/settings.json').read_text())
        pre = [hook['command'] for group in claude['hooks']['PreToolUse'] for hook in group['hooks']]
        post = [hook['command'] for group in claude['hooks']['PostToolUse'] for hook in group['hooks']]
        self.assertTrue(any('scripts/tool-guard.py' in command for command in pre))
        self.assertTrue(any('scripts/bench-hook.py' in command and '--pre' in command for command in pre))
        self.assertTrue(any('scripts/bench-hook.py' in command and '--post' in command for command in post))
        self.assertFalse([command for command in post if '--emergency' in command])


if __name__ == '__main__':
    unittest.main()
