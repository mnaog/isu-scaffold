import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import re
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
import board_data
import runner
import store
import activity


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
        self.assertEqual(data['score_history'][0]['hypothesis'], 'large text')
        self.assertEqual(data['running'][0]['id'], 'active')

    def test_running_is_kept_before_first_completed_benchmark(self):
        with patch.object(inputs, 'command_json', return_value={'runs': [{'id': 'active', 'state': 'running'}]}):
            data = inputs.metrics(self.repo, self.cfg)
        self.assertEqual(data['running'][0]['id'], 'active')
        self.assertIsNone(data['latest'])

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

    def test_current_cli_does_not_initialize_operations_database(self):
        shutil.rmtree(self.repo/'.local/operations')
        result = subprocess.run([sys.executable, str(self.repo/'scripts/operations/main.py'), 'current'],
                                cwd=self.repo, text=True, capture_output=True, check=True)
        self.assertIn('metrics_error', json.loads(result.stdout))
        self.assertFalse((self.repo/'.local/operations').exists())

    def test_activity_requires_held_lock_and_separates_deploy_record(self):
        local = self.repo/'.local'
        self.assertIsNone(activity.activity(self.repo)['operation'])
        self.assertFalse((local/'operation.lock').exists())
        lockdir = local/'operation.lock'
        lockdir.mkdir()
        (lockdir/'owner').write_text('pid=1\nstarted_at=2026-10-06T10:00:00+0900\noperation=deploy.sh\n')
        with (lockdir/'lock').open('wb') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(activity.activity(self.repo)['operation']['operation'], 'deploy.sh')
        self.assertIsNone(activity.activity(self.repo)['operation'])
        transactions = local/'deploy-transactions'
        transactions.mkdir()
        (transactions/'release.state').write_text('switching')
        (transactions/'release.commit').write_text('new-commit')
        (local/'current-deploy-commit').write_text('previous-success')
        result = activity.activity(self.repo)
        self.assertEqual(result['deploy']['phase'], 'switching')
        self.assertIsNone(result['operation'])
        self.assertEqual(result['last_deploy_commit'], 'previous-success')

    def test_auto_comparison_tracks_analysis_and_manual_none_stays_none(self):
        def fake(repo, argv, **kwargs):
            if 'brief' in argv:
                return {'run': {'id': argv[2], 'state': 'complete'},
                        'review': {'latest_analysis': {'base_run_id': 'analysis-base'}}}
            return {'rows': []}
        with patch.object(board_data, 'command_json', fake):
            auto = board_data.detail(self.repo, self.cfg, 'candidate', 'auto')
            manual = board_data.detail(self.repo, self.cfg, 'candidate', 'chosen')
            none = board_data.detail(self.repo, self.cfg, 'candidate', '')
        self.assertEqual(auto['base']['id'], 'analysis-base')
        self.assertEqual(auto['base_mode'], 'analysis')
        self.assertEqual(manual['base']['id'], 'chosen')
        self.assertIsNone(none['base'])

    def test_auto_comparison_without_analysis_does_not_guess(self):
        def fake(repo, argv, **kwargs):
            if 'brief' in argv: return {'run': {'id': argv[2], 'state': 'complete'}}
            return {'rows': []}
        with patch.object(board_data, 'command_json', fake):
            result = board_data.detail(self.repo, self.cfg, 'candidate', 'auto')
        self.assertIsNone(result['base'])

    def test_board_detail_resolves_ids_and_keeps_scout_queries_small(self):
        calls = []
        def fake(repo, argv, **kwargs):
            calls.append((argv, kwargs))
            if 'sql' in argv: return {'rows': [{'id': 'initial-survey'}]}
            if 'series' in argv: return {'rows': [], 'window': {'name': 'load'}, 'truncated': False}
            if 'brief' in argv:
                return {'run': {'id': 'resolved-' + argv[2], 'state': 'complete', 'score': 0},
                        'review': {'latest_analysis': {'body': 'evidence'}}}
            return {'rows': [{'presence': 'removed', 'base': {'total_ms': 12}, 'candidate': None}],
                    'total_count': 101, 'truncated': True}
        with patch.object(board_data, 'command_json', fake):
            data = board_data.detail(self.repo, self.cfg, 'candidate', 'baseline', 100)
        self.assertEqual(data['latest']['id'], 'resolved-candidate')
        self.assertEqual(data['base']['score'], 0)
        self.assertEqual(data['brief']['review']['latest_analysis']['body'], 'evidence')
        queries = [c for c, _ in calls if 'query' in c and '--base' in c]
        self.assertEqual(len(queries), 2)
        self.assertTrue(all(c[2] == 'resolved-candidate' and c[-1] == 'resolved-baseline' for c in queries))
        self.assertIn('--window', queries[1])
        self.assertNotIn('--group-by', queries[1])
        self.assertTrue(data['sections']['sql']['data']['truncated'])
        self.assertIsNone(data['sections']['sql']['data']['rows'][0]['candidate'])
        self.assertTrue(all(options['max_bytes'] == 2_000_000 for _, options in calls))
        self.assertIsNone(self.cfg.get('base_run'))
        self.assertEqual(data['survey']['run']['id'], 'resolved-initial-survey')
        self.assertIn(self.cfg['isuscope'] + ['query', 'resolved-candidate', '--view', 'http', '--limit', '500'], [c for c, _ in calls])
        self.assertIn(self.cfg['isuscope'] + ['query', 'initial-survey', '--metric-prefix', 'transition.', '--limit', '1000'], [c for c, _ in calls])
        series = [c for c, _ in calls if 'series' in c]
        self.assertEqual(len(series), 2)
        self.assertTrue(all(c[2] == 'resolved-candidate' and '--limit' in c and '--window' in c for c in series))
        self.assertTrue(all(c[1] in ('brief', 'query', 'series', 'sql') for c, _ in calls))

    def test_board_detail_rejects_invalid_base_without_silent_fallback(self):
        def fake(repo, argv, **kwargs):
            if argv[2] == 'bad': return {'error': 'unknown run'}
            return {'run': {'id': 'ok', 'state': 'complete'}}
        with patch.object(board_data, 'command_json', fake):
            self.assertEqual(board_data.detail(self.repo, self.cfg, 'ok', 'bad')['error'], 'unknown run')
        with patch.object(board_data, 'command_json', return_value={'run': {'id': 'active', 'state': 'running'}}):
            self.assertIn('error', board_data.detail(self.repo, self.cfg, 'active'))

    def test_board_optional_measurements_fail_independently(self):
        def fake(repo, argv, **kwargs):
            if 'brief' in argv: return {'run': {'id': 'candidate', 'state': 'complete'}}
            if 'series' in argv: return {'error': 'load window unavailable'}
            if 'sql' in argv: return {'rows': []}
            return {'rows': [], 'total_count': 0}
        with patch.object(board_data, 'command_json', fake):
            data = board_data.detail(self.repo, self.cfg, 'candidate')
        self.assertEqual(data['latest']['id'], 'candidate')
        self.assertNotIn('error', data['sections']['http']['data'])
        self.assertEqual(data['timeline']['error'], 'load window unavailable')
        self.assertIsNone(data['survey']['run'])

    def test_board_selection_is_bounded_and_cannot_inject_cli_options(self):
        for params in ({}, {'run': ['--help']}, {'run': ['a', 'b']}, {'run': ['a'], 'limit': ['9999']},
                       {'run': ['a'], 'command': ['run']}, {'run': ['a/b']}):
            with self.assertRaises(ValueError): board_data.selection(params)
        self.assertEqual(board_data.selection({'run': ['abc-123'], 'base': [''], 'limit': ['25']}), ('abc-123', '', 25))

    def test_board_cache_expires_and_bounds_memory(self):
        cache = board_data.DetailCache()
        with patch.object(board_data, 'detail', return_value={'latest': {'id': 'a'}}) as fetch, patch.object(board_data.time, 'monotonic', return_value=0):
            cache.get(self.repo, self.cfg, 'a', '', 50)
            cache.get(self.repo, self.cfg, 'a', '', 50)
            self.assertEqual(fetch.call_count, 1)
        with patch.object(board_data, 'detail', return_value={}) as fetch, patch.object(board_data.time, 'monotonic', return_value=31):
            cache.get(self.repo, self.cfg, 'a', '', 50)
            self.assertEqual(fetch.call_count, 1)
            for i in range(12): cache.get(self.repo, self.cfg, str(i), '', 50)
        self.assertEqual(len(cache.results.items), 8)

    def test_board_detail_reuses_measurements_of_a_finished_run(self):
        calls = []
        analysis = {'body': 'first'}
        def fake(repo, argv, **kwargs):
            calls.append(argv)
            if 'sql' in argv: return {'rows': [{'id': 'survey'}]}
            if 'brief' in argv:
                return {'run': {'id': argv[2], 'state': 'complete'}, 'review': {'latest_analysis': dict(analysis)}}
            return {'rows': []}
        measured = board_data.BoundedCache(8)
        with patch.object(board_data, 'command_json', fake):
            first = board_data.detail(self.repo, self.cfg, 'candidate', 'baseline', 50, measured)
            calls.clear()
            analysis['body'] = 'second'
            second = board_data.detail(self.repo, self.cfg, 'candidate', 'baseline', 50, measured)
        self.assertEqual([c[1] for c in calls], ['brief', 'brief'])
        self.assertEqual(second['brief']['review']['latest_analysis']['body'], 'second')
        self.assertEqual(second['sections'], first['sections'])
        self.assertEqual(second['survey']['run']['id'], 'survey')

    def test_board_detail_retries_measurements_that_failed(self):
        calls = []
        def fake(repo, argv, **kwargs):
            calls.append(argv)
            if 'brief' in argv: return {'run': {'id': 'candidate', 'state': 'complete'}}
            if 'series' in argv: return {'error': 'isuscope busy'}
            return {'rows': []}
        measured = board_data.BoundedCache(8)
        with patch.object(board_data, 'command_json', fake):
            board_data.detail(self.repo, self.cfg, 'candidate', '', 50, measured)
            calls.clear()
            board_data.detail(self.repo, self.cfg, 'candidate', '', 50, measured)
        self.assertIn('series', [c[1] for c in calls])

    def test_board_detail_rereads_until_the_initial_survey_exists(self):
        calls = []
        def fake(repo, argv, **kwargs):
            calls.append(argv)
            if 'brief' in argv: return {'run': {'id': 'candidate', 'state': 'complete'}}
            return {'rows': []}
        measured = board_data.BoundedCache(8)
        with patch.object(board_data, 'command_json', fake):
            board_data.detail(self.repo, self.cfg, 'candidate', '', 50, measured)
            calls.clear()
            board_data.detail(self.repo, self.cfg, 'candidate', '', 50, measured)
        self.assertIn('sql', [c[1] for c in calls])

    def test_board_detail_reads_measurements_in_parallel(self):
        barrier = threading.Barrier(2, timeout=5)
        def fake(repo, argv, **kwargs):
            if 'brief' in argv: return {'run': {'id': 'candidate', 'state': 'complete'}}
            if '--view' in argv and '--limit' in argv and argv[argv.index('--limit') + 1] == '50':
                barrier.wait()
            return {'rows': []}
        with patch.object(board_data, 'command_json', fake):
            data = board_data.detail(self.repo, self.cfg, 'candidate')
        self.assertEqual(set(data['sections']), {'http', 'sql'})

    def test_board_cache_serves_other_selections_while_one_is_slow(self):
        cache = board_data.DetailCache()
        release = threading.Event()
        calls = []
        def slow(repo, cfg, run, base, limit, measured=None):
            calls.append(run)
            if run == 'slow': release.wait(5)
            return {'latest': {'id': run}}
        with patch.object(board_data, 'detail', slow), concurrent.futures.ThreadPoolExecutor(3) as pool:
            waiting = [pool.submit(cache.get, self.repo, self.cfg, 'slow', '', 50) for _ in range(2)]
            fast = pool.submit(cache.get, self.repo, self.cfg, 'fast', '', 50)
            self.assertEqual(fast.result(timeout=2)['latest']['id'], 'fast')
            release.set()
            self.assertTrue(all(f.result(timeout=5)['latest']['id'] == 'slow' for f in waiting))
        self.assertEqual(calls.count('slow'), 1)

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
                self.assertIn('id="run-select"',page)
                self.assertIn('id="base-select"',page)
                self.assertIn('textContent',page)
            with urllib.request.urlopen(url+'/board-workspace.js') as r:
                self.assertIn('workspaceData', r.read().decode())
            with urllib.request.urlopen(url+'/board-details.js') as r:
                self.assertIn('openMetric', r.read().decode())
            with urllib.request.urlopen(url+'/board-scenario.js') as r:
                self.assertIn('buildScenario', r.read().decode())
            with urllib.request.urlopen(url) as r:
                self.assertEqual(r.headers['Cache-Control'], 'no-store')
                asset = re.search(r'src="(/mermaid\.tiny\.js\?v=[0-9a-f]{12})"', r.read().decode()).group(1)
            with urllib.request.urlopen(url+asset) as r:
                self.assertIn('immutable', r.headers['Cache-Control'])
                self.assertGreater(len(r.read()), 100000)
            with self.assertRaises(urllib.error.HTTPError) as invalid:
                urllib.request.urlopen(url+'/api/metrics?run=--help')
            self.assertEqual(invalid.exception.code,400)
            invalid.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(urllib.request.Request(url+'/api/metrics?run=a',data=b'{}'))
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
