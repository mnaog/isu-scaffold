import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent

# Stands in for `claude -p`: records how it was called, commits like a lane would, and
# prints the stream-json events the runner reads.
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, time
prompt = sys.stdin.read()
open(os.environ['FAKE_RECORD'], 'w').write(json.dumps({'argv': sys.argv[1:], 'prompt': prompt, 'cwd': os.getcwd(),
    'lane': os.environ.get('ISUCON_LANE'), 'claudecode': os.environ.get('CLAUDECODE')}))
if os.environ.get('FAKE_SLEEP'):
    time.sleep(float(os.environ['FAKE_SLEEP']))
os.makedirs('docs', exist_ok=True)
open('docs/code-map.md', 'w').write('# map\n')
subprocess.run(['git', 'add', 'docs/code-map.md'], check=True)
subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-qm', 'Map the code'], check=True)
if os.environ.get('FAKE_AFTER_COMMIT'):
    time.sleep(float(os.environ['FAKE_AFTER_COMMIT']))
os.makedirs('docs/agent-history', exist_ok=True)
open('docs/agent-history/lane.md', 'w').write('history written after the last commit\n')
print(json.dumps({'type': 'system', 'subtype': 'init'}))
print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': '上位の経路: GET /api/x',
                  'session_id': 'abc', 'num_turns': 3, 'total_cost_usd': 0.5}))
'''


class LaneConfigurationTests(unittest.TestCase):
    def test_every_lane_has_a_prompt_and_kickoff_names_defined_lanes(self):
        config = json.loads((ROOT / 'config/lanes.json').read_text())
        for name, lane in config['lanes'].items():
            with self.subTest(name):
                self.assertRegex(name, r'^[a-z0-9][a-z0-9-]*$')
                self.assertTrue((ROOT / lane['prompt']).is_file())
                self.assertTrue(lane['purpose'])
        self.assertLessEqual(set(config['kickoff']), set(config['lanes']))
        # scenario needs the official documents main saves, so main starts it, not kickoff.
        self.assertNotIn('scenario', config['kickoff'])


class LaneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / 'repo'
        (self.repo / 'scripts').mkdir(parents=True)
        (self.repo / 'config/lanes').mkdir(parents=True)
        for name in ('lane.py', 'progress.py', 'worktree.sh'):
            shutil.copy(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        self.claude = Path(self.tmp.name) / 'fake-claude'
        self.claude.write_text(FAKE_CLAUDE)
        self.claude.chmod(0o755)
        self.record = Path(self.tmp.name) / 'record.json'
        config = json.loads((ROOT / 'config/lanes.json').read_text())
        config['claude']['command'] = str(self.claude)
        config['lanes'] = {'code-map': {'purpose': '地図を書く', 'prompt': 'config/lanes/code-map.md', 'timeout_minutes': 1}}
        config['kickoff'] = ['code-map']
        (self.repo / 'config/lanes.json').write_text(json.dumps(config))
        (self.repo / 'config/lanes/code-map.md').write_text('docs/code-map.mdを書く。\n')
        (self.repo / '.gitignore').write_text('.local/\n')
        for args in (['init', '-q', '-b', 'main'], ['add', '.'],
                     ['-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-qm', 'fixture']):
            subprocess.run(['git', '-C', str(self.repo), *args], check=True)
        self.env = {k: v for k, v in os.environ.items() if k not in ('ISUCON_LANE', 'KICKOFF_LANES', 'PROGRESS_LOG')}
        self.env.update(FAKE_RECORD=str(self.record), CLAUDECODE='1', GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.com',
                        GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.com')

    def tearDown(self):
        self.tmp.cleanup()

    def lane(self, *args, **env):
        return subprocess.run(['python3', 'scripts/lane.py', *args], cwd=self.repo, capture_output=True,
                              text=True, env=dict(self.env, **env), timeout=60)

    def status(self):
        return json.loads((self.repo / '.local/lanes/code-map/status.json').read_text())

    def test_lane_runs_claude_in_its_own_worktree_and_records_the_result(self):
        result = self.lane('run', 'code-map')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        status = self.status()
        self.assertEqual(status['state'], 'complete', status)
        worktree = Path(status['worktree'])
        self.assertEqual(worktree.resolve(), (self.repo.parent / 'repo-code-map').resolve())
        self.assertEqual(status['branch'], 'lane/code-map')
        call = json.loads(self.record.read_text())
        self.assertEqual(Path(call['cwd']).resolve(), worktree.resolve())
        self.assertEqual(call['lane'], 'code-map')
        self.assertIsNone(call['claudecode'])
        argv = call['argv']
        for flag, value in [('--model', 'claude-opus-5-5'), ('--effort', 'high'), ('--permission-mode', 'dontAsk')]:
            self.assertEqual(argv[argv.index(flag) + 1], value)
        self.assertIn('Bash(go:*)', argv[argv.index('--allowedTools') + 1])
        self.assertEqual(argv[-2], '--allowedTools')
        self.assertIn('docs/code-map.mdを書く', call['prompt'])
        # The map and the conversation written after the last commit both stay on the branch; main is untouched.
        self.assertEqual([c.split(' ', 1)[1] for c in status['commits']], ['Record the lane conversation', 'Map the code'])
        self.assertFalse((self.repo / 'docs/code-map.md').exists())
        self.assertEqual((self.repo / '.local/lanes/code-map/result.md').read_text().strip(), '上位の経路: GET /api/x')
        log = (self.repo / '.local/progress.log').read_text()
        self.assertIn('lane:code-map\trunning', log)
        self.assertIn('lane:code-map\tcomplete\t2 commits on lane/code-map', log)
        self.assertIn('Map the code (git cherry-pick', log)
        timing = (self.repo / '.local/stage-timing.tsv').read_text().split('\t')
        self.assertEqual(timing[1:3], ['lane', 'code-map'])

    def test_each_commit_is_announced_while_the_lane_still_runs(self):
        result = self.lane('run', 'code-map', FAKE_AFTER_COMMIT='2', LANE_POLL_SECONDS='0.2')
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [line.split('\t') for line in (self.repo / '.local/progress.log').read_text().splitlines()]
        states = [row[2] for row in rows if row[1] == 'lane:code-map']
        # The commit line comes before the lane completes, and only once.
        self.assertLess(states.index('commit'), states.index('complete'))
        self.assertEqual(len([row for row in rows if row[2] == 'commit' and 'Map the code' in row[3]]), 1)

    def test_a_running_lane_shows_under_working_on_the_board_until_it_ends(self):
        for name in ('proposal.py', 'main-session.py', 'isuscope_sql.py'):
            shutil.copy(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        read = ("import importlib.util, json; spec = importlib.util.spec_from_file_location('p', 'scripts/proposal.py'); "
                "p = importlib.util.module_from_spec(spec); spec.loader.exec_module(p); print(json.dumps(p.working_now('.')))")

        def working():
            done = subprocess.run(['python3', '-c', read], cwd=self.repo, capture_output=True, text=True, check=True)
            return [(e['name'], e['role'], e['branch'], e['what']) for e in json.loads(done.stdout)]

        lane = subprocess.Popen(['python3', 'scripts/lane.py', 'run', 'code-map'], cwd=self.repo,
                                env=dict(self.env, FAKE_SLEEP='3'), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not working():
            time.sleep(0.2)
        self.assertEqual(working(), [('code-map', 'lane', 'lane/code-map', '地図を書く')])
        self.assertEqual(lane.wait(timeout=60), 0)
        self.assertEqual(working(), [])
        # A declaration left by a runner that died shows only while the lane's state says it runs.
        declared = self.repo / '.local/proposal-state/working/lane-code-map.json'
        declared.write_text(json.dumps({'name': 'code-map', 'role': 'lane', 'worktree': self.status()['worktree'],
                                        'branch': 'lane/code-map', 'what': '地図を書く'}))
        self.assertEqual(working(), [])

    def test_timeout_stops_claude_and_is_recorded(self):
        config = json.loads((self.repo / 'config/lanes.json').read_text())
        config['lanes']['code-map']['timeout_minutes'] = 0.02
        (self.repo / 'config/lanes.json').write_text(json.dumps(config))
        result = self.lane('run', 'code-map', FAKE_SLEEP='30')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.status()['state'], 'timeout')
        self.assertIn('lane:code-map\ttimeout', (self.repo / '.local/progress.log').read_text())

    def test_kickoff_starts_configured_lanes_once_and_can_be_disabled(self):
        self.assertIn('disabled', self.lane('kickoff', KICKOFF_LANES='').stdout)
        self.assertFalse((self.repo / '.local/lanes/code-map').exists())
        started = self.lane('kickoff')
        self.assertEqual(started.returncode, 0, started.stderr)
        for _ in range(200):
            if (self.repo / '.local/lanes/code-map/status.json').exists() and self.status()['state'] in ('complete', 'failed'):
                break
            subprocess.run(['sleep', '0.1'])
        self.assertEqual(self.status()['state'], 'complete')
        self.assertIn('already complete', self.lane('kickoff').stdout)

    def test_a_lane_cannot_start_lanes_and_missing_claude_is_reported(self):
        refused = self.lane('start', 'code-map', ISUCON_LANE='other')
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('does not start other lanes', refused.stderr)
        self.claude.unlink()
        missing = self.lane('start', 'code-map')
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn('lane:code-map\tfailed', (self.repo / '.local/progress.log').read_text())

    def test_a_lane_waits_for_required_files_and_gets_its_extra_tools(self):
        config = json.loads((self.repo / 'config/lanes.json').read_text())
        config['lanes']['code-map'].update(requires=['docs/state-log.md'], extra_allowed_tools=['Bash(git merge:*)'])
        (self.repo / 'config/lanes.json').write_text(json.dumps(config))
        refused = self.lane('start', 'code-map')
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('docs/state-log.md', refused.stderr)
        self.assertFalse((self.repo / '.local/lanes/code-map').exists())
        (self.repo / 'docs').mkdir()
        (self.repo / 'docs/state-log.md').write_text('# states\n')
        for args in (['add', '.'], ['commit', '-qm', 'Take in state-log']):
            subprocess.run(['git', '-C', str(self.repo), *args], check=True, env=self.env)
        result = self.lane('run', 'code-map')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        tools = json.loads(self.record.read_text())['argv']
        allowed = tools[tools.index('--allowedTools') + 1].split(',')
        self.assertIn('Bash(git merge:*)', allowed)
        self.assertIn('Bash(go:*)', allowed)
        self.assertIn('分岐元branch main', json.loads(self.record.read_text())['prompt'])

    def test_main_commits_a_lane_merges_in_are_not_reported_as_the_lane_s(self):
        merge = (
            "subprocess.run(['git', 'commit', '--allow-empty', '-qm', 'Main moved on'], cwd=os.environ['MAIN_REPO'], check=True)\n"
            "subprocess.run(['git', 'merge', '--no-edit', '-q', 'main'], check=True)\n"
            "if os.environ.get('FAKE_AFTER_COMMIT'):")
        self.claude.write_text(FAKE_CLAUDE.replace("if os.environ.get('FAKE_AFTER_COMMIT'):", merge, 1))
        result = self.lane('run', 'code-map', MAIN_REPO=str(self.repo))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        subjects = [c.split(' ', 1)[1] for c in self.status()['commits']]
        self.assertNotIn('Main moved on', subjects)
        self.assertIn('Map the code', subjects)
        self.assertNotIn('Main moved on', (self.repo / '.local/progress.log').read_text())


    def follow_up(self, write):
        """Lanes a, b and memory. Each fake lane commits docs/<file> with its own text."""
        config = json.loads((self.repo / 'config/lanes.json').read_text())
        lane = {'purpose': '作る', 'prompt': 'config/lanes/code-map.md', 'timeout_minutes': 1}
        config['lanes'] = {'a': lane, 'b': lane, 'memory': lane}
        (self.repo / 'config/lanes.json').write_text(json.dumps(config))
        self.claude.write_text(FAKE_CLAUDE.replace(
            "open('docs/code-map.md', 'w').write('# map\\n')\nsubprocess.run(['git', 'add', 'docs/code-map.md'], check=True)",
            f"name = os.environ['ISUCON_LANE']\npath = {write}\nopen(path, 'w').write(name + '\\n')\n"
            "subprocess.run(['git', 'add', path], check=True)").replace("'Map the code'", "'Work in ' + name").replace(
            "if os.environ.get('FAKE_AFTER_COMMIT'):",
            "if os.environ.get('FAKE_AFTER_COMMIT') and os.environ.get('FAKE_SLOW_LANE') == name:"))

    def lane_status(self, name):
        path = self.repo / f'.local/lanes/{name}/status.json'
        return json.loads(path.read_text()) if path.exists() else {}

    def wait_until_ended(self, name):
        for _ in range(300):
            if self.lane_status(name).get('state') in ('complete', 'failed', 'timeout'):
                return self.lane_status(name)
            subprocess.run(['sleep', '0.1'])
        self.fail(f'{name} did not end: {self.lane_status(name)}')

    def test_a_lane_merges_the_lanes_it_joins_by_itself_and_their_commits_stay_theirs(self):
        self.follow_up("f'docs/{name}.md'")
        config = json.loads((self.repo / 'config/lanes.json').read_text())
        config['lanes']['memory'] = dict(config['lanes']['a'], joins=['a'])
        (self.repo / 'config/lanes.json').write_text(json.dumps(config))
        self.claude.write_text(self.claude.read_text().replace(
            "if os.environ.get('FAKE_AFTER_COMMIT') and",
            "if os.environ.get('FAKE_JOIN'):\n"
            "    subprocess.run(['git', 'merge', '--no-edit', '-q', os.environ['FAKE_JOIN']], check=True)\n"
            "if os.environ.get('FAKE_AFTER_COMMIT') and", 1))
        # It does not wait for a, and a branch that does not exist yet hides none of its commits.
        self.assertEqual(self.lane('start', 'memory').returncode, 0)
        status = self.wait_until_ended('memory')
        self.assertIn('Work in memory', [c.split(' ', 1)[1] for c in status['commits']])
        call = json.loads(self.record.read_text())
        self.assertIn('同時に動いているlane aのbranch', call['prompt'])
        allowed = call['argv'][call['argv'].index('--allowedTools') + 1].split(',')
        self.assertIn('Bash(python3 scripts/lane.py status:*)', allowed)
        self.assertIn('Bash(git merge:*)', allowed)
        # Once a has ended the lane merges it; a's commits are not reported as the lane's.
        self.assertEqual(self.lane('run', 'a').returncode, 0)
        shutil.rmtree(self.repo / '.local/lanes/memory')
        subprocess.run(['git', '-C', str(self.repo), 'worktree', 'remove', '--force', status['worktree']], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'branch', '-D', 'lane/memory'], check=True, capture_output=True)
        self.assertEqual(self.lane('run', 'memory', FAKE_JOIN='lane/a').returncode, 0)
        status = self.lane_status('memory')
        worktree = Path(status['worktree'])
        self.assertEqual((worktree / 'docs/a.md').read_text(), 'a\n')
        subjects = [c.split(' ', 1)[1] for c in status['commits']]
        self.assertIn('Work in memory', subjects)
        self.assertNotIn('Work in a', subjects)
        self.assertNotIn('Work in a (git cherry-pick', (self.repo / '.local/progress.log').read_text().split('lane:a\tcomplete')[1])



if __name__ == '__main__':
    unittest.main()
