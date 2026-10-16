import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'scripts/scout'))
import inputs
import runner
import store


class ScoutTests(unittest.TestCase):
    """Fake CLIs in a temporary repository: scheduling, reports, inputs and the daemon."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name).resolve() / 'repo'
        self.repo.mkdir()
        for directory in ('scripts/scout', 'scripts/board', 'docs/roles'):
            shutil.copytree(SOURCE / directory, self.repo / directory)
        (self.repo / 'config').mkdir()
        (self.repo / 'docs/agent-history').mkdir()
        (self.repo / 'AGENTS.md').write_text('禁止資料は読まない')
        (self.repo / '.gitignore').write_text('.local/\n__pycache__/\n')
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'test')
        self.git('add', '.')
        self.git('commit', '-m', 'initial')
        self.cli = self.repo / 'fake.py'
        self.cli.write_text('import sys\nsys.stdin.read()\nprint("発見なし")\n')
        self.cfg = json.loads((SOURCE / 'config/scout.json').read_text())
        for spec in self.cfg['scouts'].values():
            spec.update(model='fake', argv=[sys.executable, str(self.cli)], output='text')
            spec.pop('unavailable', None)
        self.cfg.update(timeout_seconds=3, isuscope=['missing-isuscope-for-test'])
        (self.repo / 'config/scout.json').write_text(json.dumps(self.cfg))
        runner.initialize(self.repo, self.cfg)
        self.bundle = {'metrics': {'current_commit': self.git('rev-parse', 'HEAD'), 'latest': {'id': 'run-12345678'}, 'base': {'id': 'base-87654321'}}}

    def tearDown(self):
        if runner.active(self.repo):
            runner.stop_daemon(self.repo)
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True, stderr=subprocess.DEVNULL).strip()

    def query(self, sql, args=()):
        with store.connect(self.repo) as db:
            return store.rows(db, sql, args)

    def wait_for(self, test):
        end = time.monotonic() + 8
        while time.monotonic() < end:
            if test():
                return
            time.sleep(.05)
        self.fail('condition timeout')

    def test_two_fast_codex_scouts_wait_four_minutes_after_their_report(self):
        cfg = json.loads((SOURCE / 'config/scout.json').read_text())
        self.assertEqual(list(cfg['scouts']), ['human', 'codex', 'codex-2'])
        self.assertTrue(cfg['scouts']['human']['manual'])
        self.assertEqual(cfg['wait_after_report_seconds'], 240)
        for name in ('codex', 'codex-2'):
            spec = cfg['scouts'][name]
            self.assertEqual(spec['model'], 'gpt-6.1-sol')
            for value in ('model_reasoning_effort="high"', 'service_tier="priority"'):
                self.assertEqual(spec['argv'][spec['argv'].index(value) - 1], '-c')

    def test_worktrees_share_database(self):
        branch = self.repo.parent / 'parallel'
        self.git('worktree', 'add', '-b', 'parallel', str(branch))
        self.assertEqual(store.root(branch), self.repo)
        self.assertFalse((branch / '.local').exists())

    def test_latest_report_export_without_a_length_limit(self):
        runner.save_report(self.repo, 'codex', 'あ'*2000, 'local-ref', 'session', now=1)
        with self.assertRaises(ValueError):
            runner.save_report(self.repo, 'codex', ' \n', 'local-ref', 'session')
        self.assertEqual(self.query("SELECT report FROM scouts WHERE name='codex'")[0]['report'], 'あ'*2000)
        reports = self.repo / '.local/scout/reports.md'
        self.assertIn('あ'*2000, reports.read_text())
        runner.save_report(self.repo, 'codex', '順調そう', 'local-ref', 'session', now=2)
        text = reports.read_text()
        self.assertIn('順調そう', text)
        self.assertNotIn('あ'*2000, text)
        self.assertEqual(text.count('## '), len(self.cfg['scouts']))
        # The person's section comes first, in the configuration's order.
        self.assertLess(text.index('## human'), text.index('## codex'))
        self.assertNotIn('local-ref', text)
        # Only the time heads a report: a scout reads the live worktree, not one commit or run.
        self.assertRegex(text, r'## codex\n\n投稿: \S+Z\n\n> 順調そう')
        self.assertNotIn('commit:', text)
        # The reports stay out of Git while the scouts run; only make scout-stop leaves a snapshot.
        self.assertNotIn('docs/', self.git('status', '--porcelain', '--untracked-files=all'))

    def test_a_person_posts_as_the_human_scout_from_a_worktree(self):
        worktree = self.repo.parent / 'repo-human'
        self.git('worktree', 'add', '-b', 'human', str(worktree))
        cli = [sys.executable, str(self.repo / 'scripts/scout/main.py')]
        env = {k: v for k, v in os.environ.items() if not k.startswith(('CLAUDE_CODE_SESSION', 'CODEX_'))}
        posted = subprocess.run([*cli, 'post', 'human', 'DBより先に/api/xxxのN+1を見て'], cwd=worktree, env=env,
                                text=True, capture_output=True)
        self.assertEqual(posted.returncode, 0, posted.stderr)
        reports = (self.repo / '.local/scout/reports.md').read_text()
        self.assertIn('> DBより先に/api/xxxのN+1を見て', reports)
        self.assertFalse((self.repo / '.local/scout/emergency.json').exists())
        # A new post replaces the previous one, like a scout's latest report; stdin works too.
        subprocess.run([*cli, 'post', 'human', '--emergency'], cwd=worktree, env=env, input='止めて\n', text=True,
                       capture_output=True, check=True)
        reports = (self.repo / '.local/scout/reports.md').read_text()
        self.assertIn('> 止めて', reports)
        self.assertNotIn('N+1', reports)
        key = json.loads((self.repo / '.local/scout/emergency.json').read_text())['key']
        self.assertRegex(key, r'^human@\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertIn('投稿: ' + key.split('@')[1], reports)
        # The agents' scouts are not posted to by hand, and the daemon never runs the person.
        refused = subprocess.run([*cli, 'post', 'codex', 'x'], cwd=worktree, env=env, text=True, capture_output=True)
        self.assertNotEqual(refused.returncode, 0)
        once = subprocess.run([*cli, 'once', 'human'], cwd=self.repo, env=env, text=True, capture_output=True)
        self.assertNotEqual(once.returncode, 0)
        self.assertIn('post human', once.stderr)

    def test_stop_leaves_a_snapshot_of_the_reports_to_commit(self):
        cli = [sys.executable, str(self.repo / 'scripts/scout/main.py'), 'stop']
        subprocess.run(cli, cwd=self.repo, text=True, capture_output=True, check=True)
        self.assertFalse((self.repo / 'docs/scout-reports.md').exists())  # nothing reported yet
        runner.save_report(self.repo, 'codex', '最後の報告', 'ref', 'session')
        result = subprocess.run(cli, cwd=self.repo, text=True, capture_output=True, check=True)
        self.assertIn('docs/scout-reports.md', result.stdout)
        self.assertIn('最後の報告', (self.repo / 'docs/scout-reports.md').read_text())

    def test_start_registers_the_session_that_starts_the_scouts(self):
        env = {**os.environ, 'CLAUDE_CODE_SESSION_ID': 'main-session'}
        env.pop('CODEX_SESSION_ID', None)
        env.pop('CODEX_THREAD_ID', None)
        cli = [sys.executable, str(self.repo / 'scripts/scout/main.py'), 'start']
        result = subprocess.run(cli, cwd=self.repo, env=env, text=True, capture_output=True, check=True)
        self.assertIn('claude session main-session', result.stdout)
        self.assertEqual(self.query('SELECT agent,session_id FROM conversation_sources'),
                         [dict(agent='claude', session_id='main-session')])

    def test_the_history_of_a_scout_session_is_dropped(self):
        folder = self.repo / 'docs/agent-history'
        # What the agent-history hook writes for a scout's CLI (role scout) and for the main session.
        (folder / 'scout.events.jsonl').write_text(json.dumps({'session_id': 'x', 'role': 'scout'}) + '\n')
        (folder / 'scout.md').write_text('- Agent: `claude`\n')
        (folder / 'main.events.jsonl').write_text(json.dumps({'session_id': 'main', 'role': None}) + '\n')
        (folder / 'main.md').write_text('- Agent: `claude`\n')
        self.assertTrue(runner.attempt(self.repo, self.cfg, 'codex', bundle=self.bundle))
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['main.events.jsonl', 'main.md'])

    def test_fake_cli_posts_then_waits_after_the_report(self):
        # The wait counts from the saved report, not from the start; no wall-clock wait.
        times = iter([100.0, 140.0, 145.0])
        self.assertTrue(runner.attempt(self.repo, self.cfg, 'codex', bundle=self.bundle, clock=lambda: next(times)))
        row = self.query("SELECT * FROM scouts WHERE name='codex'")[0]
        self.assertEqual(row['posted_at'], 140)
        self.assertEqual(row['next_at'], 145 + self.cfg['wait_after_report_seconds'])
        self.assertEqual(row['report'], '発見なし')
        self.assertEqual(row['state'], 'waiting')

    def test_kimi_gets_the_prompt_as_an_argument_and_its_last_answer_is_the_report(self):
        # Kimi Code reads the prompt only from --prompt; stdin_as_arg.py appends stdin there.
        fake = self.repo / 'fake_kimi.py'
        fake.write_text('import json, sys\n'
                        'assert sys.argv[-2] == "--prompt" and "禁止資料" in sys.argv[-1]\n'
                        'for event in [{"role": "meta", "type": "system.version"},\n'
                        '              {"role": "assistant", "content": [{"type": "text", "text": "途中の考え"}]},\n'
                        '              {"role": "assistant", "tool_calls": []},\n'
                        '              {"role": "assistant", "content": "索引がない"},\n'
                        '              {"role": "meta", "type": "session.resume_hint", "session_id": "session_k"}]:\n'
                        '    print(json.dumps(event, ensure_ascii=False))\n')
        # Kimi is out of the default scouts; its adapter stays for a configuration that adds it back.
        kimi = {'label': 'Kimi Code + Kimi K3', 'model': 'kimi-code/k3', 'output': 'kimi-json',
                'argv': ['python3', 'scripts/scout/stdin_as_arg.py', 'kimi', '--model', '{model}',
                         '--output-format', 'stream-json', '--prompt']}
        self.cfg['scouts']['kimi'] = dict(kimi, argv=[sys.executable, str(SOURCE / 'scripts/scout/stdin_as_arg.py'),
                                                      sys.executable, str(fake), *kimi['argv'][3:]])
        runner.initialize(self.repo, self.cfg)
        with patch.object(runner, 'prompt', return_value='禁止資料は読まない'):
            self.assertTrue(runner.attempt(self.repo, self.cfg, 'kimi', bundle=self.bundle))
        row = self.query("SELECT * FROM scouts WHERE name='kimi'")[0]
        self.assertEqual(row['report'], '索引がない')
        self.assertEqual(row['session_id'], 'session_k')

    def test_failure_backoff_keeps_previous_report(self):
        runner.save_report(self.repo, 'codex', '順調そう', 'ref', 'session')
        self.cli.write_text('import sys\nsys.stdin.read()\nprint("  ")\n')
        wait = self.cfg['wait_after_report_seconds']
        delays = [min(self.cfg['retry_max_seconds'], wait * 2 ** n) for n in range(5)]
        for attempt, delay in enumerate(delays, 1):
            self.assertFalse(runner.attempt(self.repo, self.cfg, 'codex', bundle=self.bundle, clock=lambda: 100))
            row = self.query("SELECT * FROM scouts WHERE name='codex'")[0]
            self.assertEqual(row['next_at'], 100+delay)
            self.assertEqual(row['failures'], attempt)
            self.assertEqual(row['report'], '順調そう')
            self.assertIn('空', row['error'])

    def test_same_scout_lock_and_cancel_running_cli(self):
        self.cli.write_text('import time\ntime.sleep(60)\n')
        stop = threading.Event()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(runner.attempt, self.repo, self.cfg, 'codex', stop, self.bundle)
            self.wait_for(lambda: self.query("SELECT state FROM scouts WHERE name='codex'")[0]['state']=='running')
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                runner.attempt(self.repo, self.cfg, 'codex', bundle=self.bundle)
            stop.set()
            self.assertFalse(future.result(timeout=5))
        self.assertEqual(self.query("SELECT state FROM scouts WHERE name='codex'")[0]['state'], 'stopped')

    def test_conversation_cli_replaces_explicit_source_without_using_old_registrations(self):
        with store.connect(self.repo) as db:
            db.execute('CREATE TABLE operators(agent TEXT, session_id TEXT)')
            db.execute("INSERT INTO operators VALUES ('claude','old-session')")
        self.assertEqual(inputs.generate(self.repo, self.cfg)['conversations'], {})
        cli = [sys.executable, str(self.repo/'scripts/scout/main.py'), 'conversation', 'codex']
        for session in ('first-session', 'current-session'):
            subprocess.run(cli + [session], cwd=self.repo, check=True, capture_output=True)
        self.assertEqual(self.query('SELECT agent,session_id FROM conversation_sources'),
                         [dict(agent='codex', session_id='current-session')])
        self.assertEqual(self.query('SELECT session_id FROM operators')[0]['session_id'], 'old-session')
        rejected = subprocess.run(cli + [' '], cwd=self.repo, capture_output=True)
        self.assertNotEqual(rejected.returncode, 0)

    def test_history_matches_both_explicit_ids_not_recency(self):
        folder = self.repo / 'docs/agent-history'
        (folder / 'selected.md').write_text('- Agent: `codex`\n- Session: `selected-1`\n\nselected conversation')
        (folder / 'newer-unselected.md').write_text('- Agent: `codex`\n- Session: `unselected-2`\n\nUNSELECTED CONVERSATION')
        with store.connect(self.repo) as db:
            db.execute("INSERT INTO conversation_sources(agent,session_id) VALUES ('codex','selected-1')")
        bundle = inputs.generate(self.repo, self.cfg)
        text = json.dumps(bundle)
        self.assertIn('selected conversation', text)
        self.assertNotIn('UNSELECTED CONVERSATION', text)
        self.assertNotIn('scouts', bundle)
        self.assertIn('error', bundle['metrics'])
        self.assertNotIn('claude', bundle['conversations'])

    def test_scout_final_check_reads_only_registered_context_and_latest_analysis(self):
        runner.save_report(self.repo, 'codex', 'PRIVATE SCOUT REPORT', 'ref', 'session')
        with store.connect(self.repo) as db:
            db.execute("INSERT INTO conversation_sources(agent,session_id) VALUES ('codex','selected')")
        (self.repo/'docs/agent-history/current.md').write_text('- Agent: `codex`\n- Session: `selected`\nCURRENT CONVERSATION')
        def fake(repo, argv):
            if 'list' in argv:
                return {'runs': [{'id': 'active', 'state': 'running'}, {'id': 'new', 'state': 'complete'}]}
            self.assertEqual(argv[-3:], ['new', '--limit', '5'])
            return {'review': {'latest_analysis': {'body': 'NEW ANALYSIS'}}}
        before = self.query('SELECT * FROM scouts')
        with patch.object(inputs, 'command_json', fake):
            result = inputs.current(self.repo, self.cfg)
        self.assertEqual(result['latest']['id'], 'new')
        self.assertIn('NEW ANALYSIS', json.dumps(result))
        self.assertIn('CURRENT CONVERSATION', json.dumps(result))
        self.assertNotIn('PRIVATE SCOUT REPORT', json.dumps(result))
        self.assertEqual(before, self.query('SELECT * FROM scouts'))

    def test_current_cli_does_not_initialize_the_scout_database(self):
        shutil.rmtree(self.repo/'.local/scout')
        result = subprocess.run([sys.executable, str(self.repo/'scripts/scout/main.py'), 'current'],
                                cwd=self.repo, text=True, capture_output=True, check=True)
        self.assertIn('metrics_error', json.loads(result.stdout))
        self.assertFalse((self.repo/'.local/scout').exists())

    def test_scout_inputs_use_the_windows_the_brief_chose(self):
        calls = []
        def fake(repo, argv, **kwargs):
            calls.append(argv)
            if 'list' in argv: return {'runs': [{'id': 'r', 'state': 'complete', 'passed': True}]}
            if 'brief' in argv: return {'run': 'r', 'database': {'window': 'whole'}, 'hosts': {'window': 'whole'}}
            return {'rows': []}
        with patch.object(inputs, 'command_json', fake):
            inputs.metrics(self.repo, self.cfg)
        used = {argv[argv.index('--window') + 1] for argv in calls if '--window' in argv}
        self.assertEqual(used, {'whole'})

    def test_daemon_restart_preserves_deadline_and_multiple_start_stops(self):
        runner.start(self.repo)
        # The daemon runs the agents; the person (human, manual) only posts by hand.
        self.wait_for(lambda: all(r['state']=='waiting' for r in self.query("SELECT state FROM scouts WHERE name!='human'")))
        self.assertEqual(self.query("SELECT state,report FROM scouts WHERE name='human'"), [{'state': 'stopped', 'report': None}])
        with self.assertRaisesRegex(RuntimeError,'already running'):
            runner.start(self.repo)
        runner.stop_daemon(self.repo)
        self.assertFalse(runner.active(self.repo))
        old=self.query('SELECT posted_at,next_at FROM scouts ORDER BY name')
        runner.start(self.repo)
        time.sleep(.4)
        self.assertEqual(old,self.query('SELECT posted_at,next_at FROM scouts ORDER BY name'))
        runner.stop_daemon(self.repo)
        self.assertTrue(all(r['state']=='stopped' for r in self.query('SELECT state FROM scouts')))

    def test_missing_cli_and_timeout_are_visible(self):
        self.cfg['scouts']['codex']['argv']=['missing-cli-for-test']
        self.assertFalse(runner.attempt(self.repo,self.cfg,'codex',bundle=self.bundle))
        self.assertIn('No such file',self.query("SELECT error FROM scouts WHERE name='codex'")[0]['error'])
        self.cfg['scouts']['codex']['argv']=[sys.executable,str(self.cli)]
        self.cfg['timeout_seconds']=.15
        self.cli.write_text('import time\ntime.sleep(60)\n')
        self.assertFalse(runner.attempt(self.repo,self.cfg,'codex',bundle=self.bundle))
        self.assertIn('timeout',self.query("SELECT error FROM scouts WHERE name='codex'")[0]['error'])

    def test_a_failing_cli_leaves_the_reason_from_its_stderr(self):
        self.cli.write_text('import sys\nsys.stdin.read()\n'
                            'print("error: failed to run prompt: 403 You\'ve reached your 5-hour usage limit", file=sys.stderr)\n'
                            'print("See log: /tmp/cli.log", file=sys.stderr)\nsys.exit(1)\n')
        self.assertFalse(runner.attempt(self.repo, self.cfg, 'codex', bundle=self.bundle))
        error = self.query("SELECT error FROM scouts WHERE name='codex'")[0]['error']
        self.assertIn('CLI exit 1: error: failed to run prompt: 403', error)
        self.assertIn('.local/scout/runs/codex/', error)

    def test_codex_runs_in_a_sandbox_that_can_open_the_sqlite_databases(self):
        argv = json.loads((SOURCE/'config/scout.json').read_text())['scouts']['codex']['argv']
        self.assertEqual(argv[argv.index('--sandbox') + 1], 'workspace-write')

    def test_no_time_limit_by_default(self):
        self.assertIsNone(json.loads((SOURCE/'config/scout.json').read_text())['timeout_seconds'])
        self.cfg['timeout_seconds']=None
        (self.repo/'config/scout.json').write_text(json.dumps(self.cfg))
        self.assertIsNone(store.config(self.repo)['timeout_seconds'])
        self.cli.write_text('import sys,time\nsys.stdin.read()\ntime.sleep(.5)\nprint("遅い報告")\n')
        self.assertTrue(runner.attempt(self.repo,self.cfg,'codex',bundle=self.bundle))
        self.assertIn('遅い報告',self.query("SELECT report FROM scouts WHERE name='codex'")[0]['report'])

    def test_daemon_repeats_with_short_interval_in_fresh_sessions(self):
        self.cfg['wait_after_report_seconds'] = .3
        (self.repo/'config/scout.json').write_text(json.dumps(self.cfg))
        runner.start(self.repo)
        def count():
            return len(list((self.repo/'.local/scout/runs/codex').glob('*/exit.json')))
        self.wait_for(lambda: count() >= 2)
        runner.stop_daemon(self.repo)
        records=list((self.repo/'.local/scout/runs/codex').glob('*/process.json'))
        ids=[json.loads(p.read_text())['invocation_id'] for p in records]
        self.assertEqual(len(ids),len(set(ids)))
        stopped=count()
        time.sleep(.5)
        self.assertEqual(count(),stopped)

    def test_daemon_crash_terminates_cli_group(self):
        self.cli.write_text('import os,time\nfrom pathlib import Path\nPath(".local/child-"+str(os.getpid())).write_text(str(os.getpid()))\ntime.sleep(60)\n')
        pid=runner.start(self.repo)
        self.wait_for(lambda: len(list((self.repo/'.local').glob('child-*')))==len([s for s in self.cfg['scouts'].values() if not s.get('manual')]))
        children=[int(p.read_text()) for p in (self.repo/'.local').glob('child-*')]
        os.kill(pid,signal.SIGKILL)
        def all_exited():
            for child in children:
                try: os.kill(child,0)
                except ProcessLookupError: continue
                return False
            return True
        self.wait_for(all_exited)
        self.assertFalse(runner.active(self.repo))


if __name__ == '__main__':
    unittest.main()
