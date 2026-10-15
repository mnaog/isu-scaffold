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

    def hook(self, tool_input, *args, tool='Bash', session='main'):
        payload = {'tool_name': tool, 'tool_input': tool_input, 'cwd': str(self.root), 'session_id': session}
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

    def test_each_scout_report_is_handed_over_once(self):
        reports = self.root / '.local/scout/reports.md'
        post = '投稿: {} / commit: `abc` / run: `12345678` / base: `なし`\n\n> {}\n'
        write(reports, '# Scout reports\n\n## claude\n\n' + post.format('2026-10-14T00:00:00Z', '一つ目') +
              '\n## kimi\n\n' + post.format('2026-10-14T00:01:00Z', 'kimiの指摘'))
        started = {'command': 'isuscope run --hypothesis x', 'run_in_background': True}
        first = self.hook(started, '--post')['additionalContext']
        self.assertIn('一つ目', first)
        self.assertIn('kimiの指摘', first)
        # Nothing new since the last benchmark: no scout content at all.
        self.assertIsNone(self.hook(started, '--post'))
        # claude posted again: only its new report, not kimi's old one.
        write(reports, '# Scout reports\n\n## claude\n\n' + post.format('2026-10-14T00:10:00Z', '二つ目') +
              '\n## kimi\n\n' + post.format('2026-10-14T00:01:00Z', 'kimiの指摘'))
        second = self.hook(started, '--post')['additionalContext']
        self.assertIn('二つ目', second)
        self.assertNotIn('kimiの指摘', second)
        # Another session has seen nothing yet.
        other = self.hook(started, '--post', session='other')['additionalContext']
        self.assertIn('二つ目', other)
        self.assertIn('kimiの指摘', other)

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

    def test_an_emergency_report_reaches_main_at_its_next_tool_call_once(self):
        report = ('# Scout reports\n\n## human\n\n'
                  '投稿: 2026-10-15T12:00:00Z / commit: `abc` / run: `なし` / base: `なし`\n\n> deployを止めて\n')
        write(self.root / '.local/scout/reports.md', report)
        self.assertIsNone(self.hook({'command': 'ls'}, '--emergency'))  # no marker: nothing to hand over
        write(self.root / '.local/scout/emergency.json', json.dumps({'key': 'human@2026-10-15T12:00:00Z'}))
        # While another session is main, this one does not get it.
        write(self.root / '.local/main-session.json', json.dumps({'agent': 'claude', 'session': 'main'}))
        self.assertIsNone(self.hook({'command': 'ls'}, '--emergency', session='other'))
        output = self.hook({'file_path': 'x'}, '--emergency', tool='Read')
        self.assertIn('緊急', output['additionalContext'])
        self.assertIn('> deployを止めて', output['additionalContext'])
        self.assertIsNone(self.hook({'command': 'ls'}, '--emergency'))
        # The next benchmark does not hand the same report again.
        started = {'command': 'isuscope run --hypothesis x', 'run_in_background': True}
        self.assertIsNone(self.hook(started, '--post'))

    def test_claude_settings_run_both_hooks(self):
        claude = json.loads((ROOT / '.claude/settings.json').read_text())
        pre = [hook['command'] for group in claude['hooks']['PreToolUse'] for hook in group['hooks']]
        post = [hook['command'] for group in claude['hooks']['PostToolUse'] for hook in group['hooks']]
        self.assertTrue(any('scripts/tool-guard.py' in command for command in pre))
        self.assertTrue(any('scripts/bench-hook.py' in command and '--pre' in command for command in pre))
        self.assertTrue(any('scripts/bench-hook.py' in command and '--post' in command for command in post))
        # The emergency check runs after every tool, and only starts Python while a report waits.
        emergency = [group for group in claude['hooks']['PostToolUse']
                     if any('--emergency' in hook['command'] for hook in group['hooks'])]
        self.assertEqual(len(emergency), 1)
        self.assertNotIn('matcher', emergency[0])
        self.assertIn('.local/scout/emergency.json', emergency[0]['hooks'][0]['command'])


if __name__ == '__main__':
    unittest.main()
