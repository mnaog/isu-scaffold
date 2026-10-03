import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'scripts/operations'))
import inputs
import runner
import store


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name).resolve() / 'repo'
        self.repo.mkdir()
        for directory in ('scripts/operations', 'docs/roles'):
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
        self.cfg = json.loads((SOURCE / 'config/operations.json').read_text())
        for spec in self.cfg['scouts'].values():
            spec.update(model='fake', argv=[sys.executable, str(self.cli)], output='text')
            spec.pop('unavailable', None)
        self.cfg.update(interval_seconds=900, timeout_seconds=3, retry_max_seconds=3600, isuscope=['missing-isuscope-for-test'])
        (self.repo / 'config/operations.json').write_text(json.dumps(self.cfg))
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


    def test_board_ignores_legacy_research_without_deleting_it(self):
        import main
        with store.connect(self.repo) as db:
            db.execute('CREATE TABLE workers(note TEXT)')
            db.execute("INSERT INTO workers VALUES ('historical evidence')")
        state = main.status(self.repo)
        self.assertNotIn('workers', state)
        runner.export_board(self.repo)
        self.assertEqual(self.query('SELECT note FROM workers')[0]['note'], 'historical evidence')
        self.assertNotIn('Researcher', (self.repo / 'docs/scout-board.md').read_text())

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
        self.assertEqual(text.count('## '), 4)
        self.assertNotIn('local-ref', text)

    def test_fake_cli_posts_then_waits_900_seconds(self):
        # Completion clock differs from the start clock; no 15-minute wall wait.
        times = iter([100.0, 140.0, 145.0])
        self.assertTrue(runner.attempt(self.repo, self.cfg, 'codex', bundle=self.bundle, clock=lambda: next(times)))
        row = self.query("SELECT * FROM scouts WHERE name='codex'")[0]
        self.assertEqual(row['posted_at'], 140)
        self.assertEqual(row['next_at'], 1045)
        self.assertEqual(row['report'], '発見なし')
        self.assertEqual(row['state'], 'waiting')

    def test_failure_backoff_keeps_previous_report(self):
        runner.save_report(self.repo, 'codex', '順調そう', self.bundle, 'ref', 'session')
        self.cli.write_text('import sys\nsys.stdin.read()\nprint("あ"*301)\n')
        for attempt, delay in enumerate([900, 1800, 3600, 3600], 1):
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
        cli = [sys.executable, str(self.repo/'scripts/operations/main.py'), 'conversation', 'codex']
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

    def test_run_selection_and_bounded_comparisons(self):
        calls=[]
        def fake(repo, argv):
            calls.append(argv)
            if 'list' in argv:
                return {'runs': [{'id':'active','state':'running'}, {'id':'done','state':'complete','commit_hash':'abc'}]}
            if 'brief' in argv:
                return {'run': {'id':argv[2], 'state':'complete'}}
            return {'rows': []}
        cfg = dict(self.cfg, base_run='baseline')
        with patch.object(inputs, 'command_json', fake):
            data = inputs.metrics(self.repo, cfg)
        self.assertEqual(data['latest']['id'],'done')
        self.assertEqual(data['base']['id'],'baseline')
        queries=[c for c in calls if 'query' in c]
        self.assertEqual(len(queries),4)
        self.assertTrue(all('--base' in c and '--limit' in c for c in queries))
        self.assertIn('--window', data['sections']['sql']['command'])

    def test_score_history_keeps_failed_and_missing_scores_without_full_run_details(self):
        runs=[{'id':'active','state':'running'},
              {'id':'failed','state':'failed','score':None,'passed':False,'started_at':'2026-10-01T10:02:00Z'},
              {'id':'zero','state':'complete','score':0,'passed':True,'started_at':'2026-10-01T10:01:00Z'},
              {'id':'first','state':'complete','score':100,'passed':True,'started_at':'2026-10-01T10:00:00Z','hypothesis':'large text'}]
        def fake(repo, argv):
            if 'list' in argv: return {'runs':runs}
            return {}
        with patch.object(inputs,'command_json',fake):
            data=inputs.metrics(self.repo,self.cfg)
        self.assertEqual([r['id'] for r in data['score_history']],['first','zero','failed'])
        self.assertEqual(data['score_history'][1]['score'],0)
        self.assertIsNone(data['score_history'][2]['score'])
        self.assertNotIn('hypothesis',data['score_history'][0])

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
        (self.repo/'config/operations.json').write_text(json.dumps(self.cfg))
        runner.start(self.repo)
        def count():
            return len(list((self.repo/'.local/operations/runs/codex').glob('*/exit.json')))
        self.wait_for(lambda: count() >= 2)
        runner.stop_daemon(self.repo)
        records=list((self.repo/'.local/operations/runs/codex').glob('*/process.json'))
        ids=[json.loads(p.read_text())['invocation_id'] for p in records]
        self.assertEqual(len(ids),len(set(ids)))
        stopped=count()
        time.sleep(.5)
        self.assertEqual(count(),stopped)

    def test_daemon_crash_terminates_cli_group(self):
        self.cli.write_text('import os,time\nfrom pathlib import Path\nPath(".local/child-"+str(os.getpid())).write_text(str(os.getpid()))\ntime.sleep(60)\n')
        pid=runner.start(self.repo)
        self.wait_for(lambda: len(list((self.repo/'.local').glob('child-*')))==4)
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

    def test_opencode_json_selects_final_answer_and_rejects_errors(self):
        spec=self.cfg['scouts']['opencode']
        spec['output']='opencode-json'
        events=[
            {'type':'text','sessionID':'ses-1','part':{'id':'part-1','messageID':'msg-1','text':'これから調査します'}},
            {'type':'step_finish','sessionID':'ses-1','part':{'messageID':'msg-1','reason':'tool-calls'}},
            {'type':'tool_use','sessionID':'ses-1','part':{'messageID':'msg-1','text':'報告には入れない'}},
            {'type':'text','sessionID':'ses-1','part':{'id':'part-2','messageID':'msg-2','text':'発見なし'}},
            {'type':'text','sessionID':'ses-1','part':{'id':'part-2','messageID':'msg-2','text':'発見なし'}},
            {'type':'step_finish','sessionID':'ses-1','part':{'messageID':'msg-2','reason':'stop'}}
        ]
        def fake(data):
            output='\n'.join(json.dumps(e,ensure_ascii=False) for e in data)
            self.cli.write_text('import sys\nsys.stdin.read()\nprint('+repr(output)+')\n')
        fake(events)
        self.assertTrue(runner.attempt(self.repo,self.cfg,'opencode',bundle=self.bundle))
        row=self.query("SELECT * FROM scouts WHERE name='opencode'")[0]
        self.assertEqual(row['report'],'発見なし')
        self.assertEqual(row['session_id'],'ses-1')
        fake(events+[{'type':'error','error':{'name':'AuthenticationError'}}])
        self.assertFalse(runner.attempt(self.repo,self.cfg,'opencode',bundle=self.bundle))
        self.assertEqual(self.query("SELECT report FROM scouts WHERE name='opencode'")[0]['report'],'発見なし')
        fake(events[:-1])
        self.assertFalse(runner.attempt(self.repo,self.cfg,'opencode',bundle=self.bundle))

    def test_http_board_read_only(self):
        p=subprocess.Popen([sys.executable,str(self.repo/'scripts/operations/main.py'),'board','--port','0'],cwd=self.repo,stdout=subprocess.PIPE,text=True)
        try:
            url=p.stdout.readline().strip()
            with urllib.request.urlopen(url+'/api/board') as r:
                data=json.load(r)
                self.assertEqual(len(data['scouts']),4)
            with urllib.request.urlopen(url) as r:
                page=r.read().decode()
                self.assertNotIn('<button',page)
                self.assertIn('textContent',page)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(urllib.request.Request(url+'/api/board',data=b'{}'))
            self.assertEqual(caught.exception.code,501)
            caught.exception.close()
        finally:
            p.terminate();p.wait(timeout=5);p.stdout.close()

    def wait_for(self, test):
        end=time.monotonic()+8
        while time.monotonic()<end:
            if test():return
            time.sleep(.05)
        self.fail('condition timeout')


if __name__=='__main__': unittest.main()
