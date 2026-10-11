import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / 'scripts/tool-guard.py'


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class ToolGuardTest(unittest.TestCase):
    """Calls modelled on the practice-14 rule incidents, and ordinary calls that must pass."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write(self.root / '.local/ansible-inventory.json', json.dumps({'all': {
            'children': {
                'application': {'hosts': {'isucon14-app1': {'ansible_host': '18.180.96.31'}}},
                'benchmark': {'hosts': {'isucon14-bench': {'ansible_host': '18.176.209.207'}}},
            },
            'vars': {},
        }}))
        write(self.root / '.local/discovered-nodes.json', json.dumps([
            {'name': 'isucon14-app1', 'group': 'application', 'private_ip': '192.168.0.11'},
            {'name': 'isucon14-bench', 'group': 'benchmark', 'private_ip': '192.168.0.100',
             'public_ip': '18.176.209.207'},
        ]))
        write(self.root / 'config/benchmark.env', 'BENCHMARK_TRANSPORT=ssh\nBENCHMARK_NODE=isucon14-bench\n')
        write(self.root / 'config/tool-guard.json', json.dumps({
            'contest_keywords': ['isucon14'],
            'benchmarker_paths': ['/home/isucon/bench'],
            'allowed_urls': ['https://gist.github.com/organizer/isucon14-manual'],
        }))

    def tearDown(self):
        self.tmp.cleanup()

    def decide(self, tool, tool_input, root=None):
        payload = {'hook_event_name': 'PreToolUse', 'tool_name': tool, 'tool_input': tool_input,
                   'cwd': str(self.root)}
        result = subprocess.run(['python3', str(GUARD)], input=json.dumps(payload), text=True,
                                capture_output=True, env={**os.environ, 'TOOL_GUARD_ROOT': str(root or self.root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout.strip():
            return None
        output = json.loads(result.stdout)['hookSpecificOutput']
        self.assertEqual(output['hookEventName'], 'PreToolUse')
        self.assertEqual(output['permissionDecision'], 'deny')
        self.assertTrue(output['permissionDecisionReason'])
        return output['permissionDecisionReason']

    def assert_denied(self, tool, tool_input):
        self.assertIsNotNone(self.decide(tool, tool_input), f'{tool} {tool_input} was allowed')

    def assert_allowed(self, tool, tool_input):
        self.assertIsNone(self.decide(tool, tool_input), f'{tool} {tool_input} was denied')

    def test_benchmark_node_sessions_are_denied(self):
        for command in [
            # 18:03: per-second CPU sampling on the benchmark node
            './scripts/ssh-node.sh isucon14-bench "LC_ALL=C pidstat -u -h 1 3 | head -12"',
            'ssh -i .local/ssh/id -o StrictHostKeyChecking=no isucon@18.176.209.207 vmstat 1',
            'cd /tmp && timeout 30 ssh 192.168.0.100 uptime',
            'scp isucon@isucon14-bench:/home/isucon/env.sh .',
            'rsync -av isucon14-bench:/tmp/x .',
            './scripts/ssh-node.sh isucon14-app1 "ssh 192.168.0.100 mpstat 1"',
            'isuscope lock --path .local/operation.lock -- ./scripts/ssh-node.sh isucon14-bench "ls"',
            'ISUCON_ALLOW_BENCHMARK_NODE=1 ./scripts/ssh-node.sh isucon14-bench ls',
            'ansible benchmark -m shell -a uptime',
            # shlex cannot split this heredoc; the line check still sees the session
            "python3 - <<'EOF'\n# don't\nEOF\nssh isucon14-bench uptime",
        ]:
            with self.subTest(command=command):
                self.assert_denied('Bash', {'command': command})

    def test_codex_argument_lists_are_checked(self):
        self.assert_denied('Bash', {'command': ['bash', '-lc', 'ssh 192.168.0.100 uptime']})
        self.assert_allowed('Bash', {'command': ['bash', '-lc', 'isuscope brief latest']})

    def test_benchmarker_and_contest_code_are_denied(self):
        for command in [
            # 20:58: copying the benchmarker binary through an application node
            "./scripts/ssh-node.sh isucon14-app3 'nice -n 19 cat /home/isucon/bench' > .local/local-bench/bench",
            # 17:36: cloning the contest repository and reading its benchmarker
            'cd scratch && git clone -q --depth 1 https://github.com/isucon/isucon14 src/isucon14',
            'grep -rn ErrorCode src/isucon14/bench',
            'gh api repos/isucon/isucon14/contents/bench',
            'curl -sL https://raw.githubusercontent.com/isucon/isucon14/main/bench/go.mod',
            'git clone git@github.com:isucon/isucon14.git',
        ]:
            with self.subTest(command=command):
                self.assert_denied('Bash', {'command': command})
        self.assert_denied('Read', {'file_path': '/Users/x/isucon14/bench/benchmarker/world/user.go'})
        self.assert_denied('Grep', {'pattern': 'Paid', 'path': '/Users/x/isucon14-main/bench'})
        self.assert_denied('WebFetch', {'url': 'https://github.com/isucon/isucon14/tree/main/bench',
                                        'prompt': 'summarize'})
        self.assert_denied('WebFetch', {'url': 'https://example.com/isucon14-writeup', 'prompt': 'x'})
        self.assert_denied('WebSearch', {'query': 'ISUCON14 解説 マッチング'})

    def test_text_that_only_mentions_the_benchmark_is_allowed(self):
        for command in [
            "mkdir -p docs/phase1 && cat > docs/phase1/environment.md <<'EOF'\nprobe: test -x /home/isucon/bench\nsource: https://github.com/isucon/isucon14\nEOF",
            "git commit -q -F - <<'EOF'\nStop sampling with ssh-node.sh isucon14-bench\nEOF",
            'git commit -m "Remove the /home/isucon/bench copy"',
            'echo "do not read isucon14/bench" >> notes.md',
        ]:
            with self.subTest(command=command):
                self.assert_allowed('Bash', {'command': command})
        for command in [
            "python3 - <<'EOF'\nprint(open('/home/isucon/bench', 'rb').read()[:4])\nEOF",
            'cat /home/isucon/bench | strings | head',
            "cat > x.sh <<'EOF'\nls\nEOF\nssh isucon14-bench uptime",
        ]:
            with self.subTest(command=command):
                self.assert_denied('Bash', {'command': command})

    def test_ordinary_work_is_allowed(self):
        for command in [
            'isuscope run --hypothesis "index ride_statuses"',
            '.isuscope/benchmark.sh --probe',
            './scripts/ssh-node.sh isucon14-app1 "sudo systemctl status nginx"',
            'ssh isucon@18.180.96.31 uptime',
            'grep -n isucon14-bench .local/ansible-inventory.json',
            'curl -sk https://isuride.xiv.isucon.net/api/initialize',
            'git clone https://github.com/isucon/isucon13',
            'make deploy && isuscope brief latest',
        ]:
            with self.subTest(command=command):
                self.assert_allowed('Bash', {'command': command})
        self.assert_allowed('Read', {'file_path': str(self.root / 'webapp/rust/src/main.rs')})
        self.assert_allowed('Write', {'file_path': 'docs/notes.md', 'content': 'isucon14/bench is off limits'})
        self.assert_allowed('WebFetch', {'url': 'https://gist.github.com/organizer/isucon14-manual/raw',
                                         'prompt': 'save'})
        self.assert_allowed('WebSearch', {'query': 'ISUCON13 解説'})

    def test_empty_configuration_keeps_only_the_node_rule(self):
        (self.root / 'config/tool-guard.json').unlink()
        self.assert_allowed('WebSearch', {'query': 'ISUCON14 解説'})
        self.assert_allowed('Bash', {'command': 'grep -rn x src/isucon14/bench'})
        self.assert_denied('Bash', {'command': './scripts/ssh-node.sh isucon14-bench uptime'})

    def test_unreadable_input_is_let_through(self):
        result = subprocess.run(['python3', str(GUARD)], input='not json', text=True, capture_output=True,
                                env={**os.environ, 'TOOL_GUARD_ROOT': str(self.root)})
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        with tempfile.TemporaryDirectory() as empty:
            self.assertIsNone(self.decide('Bash', {'command': 'ssh 192.168.0.100 uptime'}, root=Path(empty)))

    def test_ssh_node_refuses_the_benchmark_node_without_the_adapter(self):
        inventory = self.root / '.local/ansible-inventory.json'
        env = {**os.environ, 'ANSIBLE_INVENTORY': str(inventory)}
        env.pop('ISUCON_ALLOW_BENCHMARK_NODE', None)
        refused = subprocess.run([str(ROOT / 'scripts/ssh-node.sh'), 'isucon14-bench', 'uptime'],
                                 env=env, capture_output=True, text=True)
        self.assertEqual(refused.returncode, 1)
        self.assertIn('refusing to connect to the benchmark node', refused.stderr)
        # With the adapter's variable the gate opens and the next check (the missing key) fails instead.
        allowed = subprocess.run([str(ROOT / 'scripts/ssh-node.sh'), 'isucon14-bench', 'uptime'],
                                 env={**env, 'ISUCON_ALLOW_BENCHMARK_NODE': '1'}, capture_output=True, text=True)
        self.assertNotIn('refusing', allowed.stderr)
        app = subprocess.run([str(ROOT / 'scripts/ssh-node.sh'), 'isucon14-app1', 'uptime'],
                             env=env, capture_output=True, text=True)
        self.assertNotIn('refusing', app.stderr)

    def test_hook_settings_call_the_guard(self):
        claude = json.loads((ROOT / '.claude/settings.json').read_text())
        codex = json.loads((ROOT / '.codex/hooks.json').read_text())
        for settings in (claude, codex):
            commands = [hook['command'] for group in settings['hooks']['PreToolUse'] for hook in group['hooks']]
            self.assertTrue(any('scripts/tool-guard.py' in command for command in commands))


if __name__ == '__main__':
    unittest.main()
