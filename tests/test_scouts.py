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
sys.path.insert(0, str(SOURCE / 'scripts/scouts'))
import inputs
import runner
import store


class ScoutTests(unittest.TestCase):
    """Fake CLIs in a temporary repository: scheduling, reports, inputs and the daemon."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name).resolve() / 'repo'
        self.repo.mkdir()
        for directory in ('scripts/scouts', 'scripts/board', 'docs/roles'):
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
        self.cfg = json.loads((SOURCE / 'config/scouts.json').read_text())
        for spec in self.cfg['scouts'].values():
            spec.update(model='fake', argv=[sys.executable, str(self.cli)], output='text')
            spec.pop('unavailable', None)
        self.cfg.update(timeout_seconds=3, isuscope=['missing-isuscope-for-test'])
        (self.repo / 'config/scouts.json').write_text(json.dumps(self.cfg))
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

    def test_configured_scouts_wait_eight_minutes_after_their_report(self):
        cfg = json.loads((SOURCE / 'config/scouts.json').read_text())
        self.assertEqual(sorted(cfg['scouts']), ['claude', 'codex', 'kimi'])
        self.assertEqual(cfg['interval_seconds'], 480)

    def test_worktrees_share_database(self):
        branch = self.repo.parent / 'parallel'
        self.git('worktree', 'add', '-b', 'parallel', str(branch))
        self.assertEqual(store.root(branch), self.repo)
        self.assertFalse((branch / '.local').exists())

    def test_latest_report_export_and_length(self):
        runner.save_report(self.repo, 'codex', 'あ'*300, self.bundle, 'local-ref', 'session', now=1)
        with self.assertRaises(ValueError):
            runner.save_report(self.repo, 'codex', 'あ'*301, self.bundle, 'local-ref', 'session')
        self.assertEqual(self.query("SELECT report FROM scouts WHERE name='codex'")[0]['report'], 'あ'*300)
        runner.save_report(self.repo, 'codex', '順調そう', self.bundle, 'local-ref', 'session', now=2)
        text = (self.repo / 'docs/scout-board.md').read_text()
        self.assertIn('順調そう', text)
        self.assertNotIn('あ'*300, text)
        self.assertEqual(text.count('## '), 3)
        self.assertNotIn('local-ref', text)

    def test_fake_cli_posts_then_waits_480_seconds_after_the_report(self):
        # The wait counts from the saved report, not from the start; no 8-minute wall wait.
        times = iter([100.0, 140.0, 145.0])
        self.assertTrue(runner.attempt(self.repo, self.cfg, 'codex', bundle=self.bundle, clock=lambda: next(times)))
        row = self.query("SELECT * FROM scouts WHERE name='codex'")[0]
        self.assertEqual(row['posted_at'], 140)
        self.assertEqual(row['next_at'], 145 + 480)
        self.assertEqual(row['report'], '発見なし')
        self.assertEqual(row['state'], 'waiting')

    def test_failure_backoff_keeps_previous_report(self):
        runner.save_report(self.repo, 'codex', '順調そう', self.bundle, 'ref', 'session')
        self.cli.write_text('import sys\nsys.stdin.read()\nprint("あ"*301)\n')
        for attempt, delay in enumerate([480, 960, 1920, 3600], 1):
            self.assertFalse(runner.attempt(self.repo, self.cfg, 'codex', bundle=self.bundle, clock=lambda: 100))
            row = self.query("SELECT * FROM scouts WHERE name='codex'")[0]
            self.assertEqual(row['next_at'], 100+delay)
            self.assertEqual(row['failures'], attempt)
            self.assertEqual(row['report'], '順調そう')
            self.assertIn('301', row['error'])

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
        cli = [sys.executable, str(self.repo/'scripts/scouts/main.py'), 'conversation', 'codex']
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
        runner.save_report(self.repo, 'codex', 'PRIVATE SCOUT REPORT', self.bundle, 'ref', 'session')
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
        shutil.rmtree(self.repo/'.local/scouts')
        result = subprocess.run([sys.executable, str(self.repo/'scripts/scouts/main.py'), 'current'],
                                cwd=self.repo, text=True, capture_output=True, check=True)
        self.assertIn('metrics_error', json.loads(result.stdout))
        self.assertFalse((self.repo/'.local/scouts').exists())

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
        self.wait_for(lambda: all(r['state']=='waiting' for r in self.query('SELECT state FROM scouts')))
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

    def test_daemon_repeats_with_short_interval_in_fresh_sessions(self):
        self.cfg['interval_seconds'] = .3
        (self.repo/'config/scouts.json').write_text(json.dumps(self.cfg))
        runner.start(self.repo)
        def count():
            return len(list((self.repo/'.local/scouts/runs/codex').glob('*/exit.json')))
        self.wait_for(lambda: count() >= 2)
        runner.stop_daemon(self.repo)
        records=list((self.repo/'.local/scouts/runs/codex').glob('*/process.json'))
        ids=[json.loads(p.read_text())['invocation_id'] for p in records]
        self.assertEqual(len(ids),len(set(ids)))
        stopped=count()
        time.sleep(.5)
        self.assertEqual(count(),stopped)

    def test_daemon_crash_terminates_cli_group(self):
        self.cli.write_text('import os,time\nfrom pathlib import Path\nPath(".local/child-"+str(os.getpid())).write_text(str(os.getpid()))\ntime.sleep(60)\n')
        pid=runner.start(self.repo)
        self.wait_for(lambda: len(list((self.repo/'.local').glob('child-*')))==3)
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
