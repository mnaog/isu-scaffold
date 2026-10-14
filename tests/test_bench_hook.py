import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / 'scripts/bench-hook.py'


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class BenchHookTest(unittest.TestCase):
    """Claude Code starts benchmarks in the background and gets the scout reports meanwhile."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def hook(self, tool_input, *args, tool='Bash'):
        payload = {'tool_name': tool, 'tool_input': tool_input, 'cwd': str(self.root)}
        env = {k: v for k, v in os.environ.items() if k != 'ISUCON_LANE'}
        env['TOOL_GUARD_ROOT'] = str(self.root)
        result = subprocess.run(['python3', str(HOOK), *args], input=json.dumps(payload), text=True,
                                capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        return json.loads(result.stdout)['hookSpecificOutput'] if result.stdout.strip() else None

    def test_benchmarks_start_only_in_the_background(self):
        for command in [
            'isuscope run --hypothesis "indexを足す"',
            'isuscope survey-run --hypothesis "初期状態"',
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

    def test_a_background_benchmark_hands_over_the_scout_reports(self):
        reports = self.root / '.local/scout/reports.md'
        write(reports, '# Scout reports\n\n## claude\n\n'
              '投稿: 2026-10-14T00:00:00Z / commit: `abc` / run: `12345678` / base: `なし`\n\n> indexが効いていない\n'
              '\n## kimi\n\n未投稿\n')
        started = {'command': 'isuscope run --hypothesis x', 'run_in_background': True}
        output = self.hook(started, '--post')
        self.assertEqual(output['hookEventName'], 'PostToolUse')
        self.assertIn('並列で動かしている調査エージェント', output['additionalContext'])
        self.assertIn('indexが効いていない', output['additionalContext'])
        self.assertRegex(output['additionalContext'], r'投稿: 2026-10-14T00:00:00Z（\d+分前）')
        for tool_input in [{'command': 'isuscope run --hypothesis x'}, {'command': 'make build', 'run_in_background': True}]:
            with self.subTest(tool_input=tool_input):
                self.assertIsNone(self.hook(tool_input, '--post'))
        # tool-guard keeps its own rules (a benchmark node here); bench-hook only looks for benchmarks.
        self.assertIsNone(self.hook({'command': 'ssh bench && isuscope run', 'run_in_background': True}, '--pre'))
        self.assertNotIn('benchmark-scenario.md', output['additionalContext'])  # no map written yet
        # No map and no report yet (scouts not started, or none has posted): nothing to add.
        write(reports, '# Scout reports\n\n## claude\n\n未投稿\n')
        self.assertIsNone(self.hook(started, '--post'))
        reports.unlink()
        self.assertIsNone(self.hook(started, '--post'))

    def test_a_background_benchmark_asks_to_review_the_scenario_map(self):
        started = {'command': 'isuscope run --hypothesis x', 'run_in_background': True}
        write(self.root / 'docs/benchmark-scenario.md', '# ベンチの見取り図\n')
        output = self.hook(started, '--post')
        self.assertIn('docs/benchmark-scenario.md', output['additionalContext'])
        self.assertNotIn('調査エージェント', output['additionalContext'])
        # With scout reports, the review comes first and the reports follow.
        write(self.root / '.local/scout/reports.md', '# Scout reports\n\n## codex\n\n'
              '投稿: 2026-10-14T00:00:00Z / commit: `abc` / run: `12345678` / base: `なし`\n\n> 遅い\n')
        context = self.hook(started, '--post')['additionalContext']
        self.assertLess(context.index('docs/benchmark-scenario.md'), context.index('調査エージェント'))
        self.assertIn('> 遅い', context)
        self.assertIsNone(self.hook({'command': 'isuscope run --hypothesis x'}, '--post'))

    def test_claude_settings_run_both_hooks(self):
        claude = json.loads((ROOT / '.claude/settings.json').read_text())
        pre = [hook['command'] for group in claude['hooks']['PreToolUse'] for hook in group['hooks']]
        post = [hook['command'] for group in claude['hooks']['PostToolUse'] for hook in group['hooks']]
        self.assertTrue(any('scripts/tool-guard.py' in command for command in pre))
        self.assertTrue(any('scripts/bench-hook.py' in command and '--pre' in command for command in pre))
        self.assertTrue(any('scripts/bench-hook.py' in command and '--post' in command for command in post))


if __name__ == '__main__':
    unittest.main()
