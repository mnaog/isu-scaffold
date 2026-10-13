import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
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
        self.assertIn('Bash(cargo:*)', argv[argv.index('--allowedTools') + 1])
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


if __name__ == '__main__':
    unittest.main()
