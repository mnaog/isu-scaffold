import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'scripts/board'))
import reads
import board_data
import activity
import records


class BoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name).resolve() / 'repo'
        self.repo.mkdir()
        shutil.copytree(SOURCE / 'scripts/board', self.repo / 'scripts/board')
        for name in ('proposal.py', 'main-session.py', 'progress.py'):
            shutil.copy(SOURCE / 'scripts' / name, self.repo / 'scripts' / name)
        (self.repo / 'config').mkdir()
        shutil.copy(SOURCE / 'config/records.json', self.repo / 'config/records.json')
        (self.repo / '.gitignore').write_text('.local/\n__pycache__/\n')
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'test')
        self.git('add', '.')
        self.git('commit', '-m', 'initial')
        self.cfg = {'isuscope': ['missing-isuscope-for-test']}

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True, stderr=subprocess.DEVNULL).strip()

    def test_worktrees_share_database(self):
        branch = self.repo.parent / 'parallel'
        self.git('worktree', 'add', '-b', 'parallel', str(branch))
        self.assertEqual(reads.root(branch), self.repo)
        self.assertFalse((branch / '.local').exists())

    def test_metrics_read_only_the_list_and_the_latest_brief(self):
        calls=[]
        def fake(repo, argv):
            calls.append(argv)
            if 'list' in argv:
                return {'runs': [{'id':'active','state':'running'}, {'id':'done','state':'complete','commit_hash':'abc'}]}
            if 'brief' in argv:
                return {'run': argv[2], 'summary': {'state':'complete'}}
            return {'rows': []}
        with patch.object(reads, 'command_json', fake):
            data = reads.metrics(self.repo, self.cfg)
        self.assertEqual(data['latest']['id'],'done')
        self.assertEqual([c[1] for c in calls], ['list', 'brief'])

    def test_score_history_keeps_failed_and_missing_scores_without_full_run_details(self):
        runs=[{'id':'active','state':'running'},
              {'id':'failed','state':'failed','score':None,'passed':False,'started_at':'2026-10-01T10:02:00Z'},
              {'id':'zero','state':'complete','score':0,'passed':True,'started_at':'2026-10-01T10:01:00Z'},
              {'id':'first','state':'complete','score':100,'passed':True,'started_at':'2026-10-01T10:00:00Z','hypothesis':'large text'}]
        def fake(repo, argv):
            if 'list' in argv: return {'runs':runs}
            return {}
        with patch.object(reads,'command_json',fake):
            data=reads.metrics(self.repo,self.cfg)
        self.assertEqual([r['id'] for r in data['score_history']],['first','zero','failed'])
        self.assertEqual(data['score_history'][1]['score'],0)
        self.assertIsNone(data['score_history'][2]['score'])
        self.assertEqual(data['score_history'][0]['hypothesis'], 'large text')
        self.assertEqual(data['running'][0]['id'], 'active')

    def test_running_is_kept_before_first_completed_benchmark(self):
        with patch.object(reads, 'command_json', return_value={'runs': [{'id': 'active', 'state': 'running'}]}):
            data = reads.metrics(self.repo, self.cfg)
        self.assertEqual(data['running'][0]['id'], 'active')
        self.assertIsNone(data['latest'])

    def test_activity_requires_held_lock_and_separates_deploy_record(self):
        local = self.repo/'.local'
        self.assertIsNone(activity.activity(self.repo)['operation'])
        self.assertFalse((local/'operation.lock').exists())
        lockdir = local/'operation.lock'
        lockdir.mkdir(parents=True)
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
        self.assertEqual(result['deploy']['stage'], 'switching')
        self.assertIsNone(result['operation'])
        self.assertEqual(result['last_deploy_commit'], 'previous-success')

    def test_auto_comparison_tracks_analysis_and_manual_none_stays_none(self):
        def fake(repo, argv, **kwargs):
            if 'brief' in argv:
                return {'run': argv[2], 'summary': {'state': 'complete'},
                        'review': {'latest_analysis': {'base': 'analysis-base'}}}
            return {'rows': []}
        with patch.object(board_data, 'command_json', fake):
            auto = board_data.detail(self.repo, self.cfg, 'candidate', 'auto')
            manual = board_data.detail(self.repo, self.cfg, 'candidate', 'chosen')
            none = board_data.detail(self.repo, self.cfg, 'candidate', '')
        self.assertEqual(auto['base']['short_id'], 'analysis-base')
        self.assertEqual(auto['base_mode'], 'analysis')
        self.assertEqual(manual['base']['short_id'], 'chosen')
        self.assertIsNone(none['base'])

    def test_board_restores_text_that_brief_cut_for_ai(self):
        run = 'b7c5d88e'
        calls = []
        def fake(repo, argv, **kwargs):
            calls.append(argv)
            if 'brief' in argv:
                return {'run': run, 'summary': {'state': 'complete'}, 'review': {
                    'latest_analysis': {'body': '冒頭…', 'full_text': 'isuscope sql ...'},
                    'changes': {'total_count': 3, 'truncated': False, 'rows': [
                        {'id': 'cut', 'description': '説明…', 'reason': '理由…', 'full_text': 'x'},
                        {'id': 'same', 'description': '短い', 'reason_same_as_analysis': True, 'full_text': 'x'},
                        {'id': 'short', 'description': '短い', 'reason': '短い理由'}]}}}
            if 'sql' in argv and f"LIKE '%{run}'" in argv[-1]:
                return {'rows': [{'body': '分析の全文'}]}
            if argv[-3:-1] == ['change', 'show']:
                return {'change': {'description': f'{argv[-1]}の全文'},
                        'decisions': {'total_count': 2, 'truncated': False,
                                      'rows': [{'reason': '古い理由'}, {'reason': '最新の理由'}]}}
            return {'rows': []}
        with patch.object(board_data, 'command_json', fake):
            result = board_data.detail(self.repo, self.cfg, run, '')
        review = result['brief']['review']
        self.assertEqual(review['latest_analysis']['body'], '分析の全文')
        cut, same, short = review['changes']['items']
        self.assertEqual((cut['description'], cut['reason']), ('cutの全文', '最新の理由'))
        self.assertNotIn('reason', same)
        self.assertEqual(short['reason'], '短い理由')
        # 切られていない変更は取り直さない。
        self.assertNotIn(['change', 'show', 'short'], [argv[-3:] for argv in calls])

    def test_board_reads_the_windows_the_brief_chose(self):
        for windows, expected in (({'database': {'window': 'load'}, 'hosts': {'window': 'load'}}, 'load'),
                                  ({'database': {'window': 'whole'}, 'hosts': {'window': 'whole'}}, 'whole'),
                                  ({}, 'whole')):
            calls = []
            def fake(repo, argv, **kwargs):
                calls.append(argv)
                if 'brief' in argv: return {'run': argv[2], 'summary': {'state': 'complete'}, **windows}
                return {'rows': []}
            with patch.object(board_data, 'command_json', fake):
                board_data.detail(self.repo, self.cfg, 'candidate', '')
            used = {argv[argv.index('--window') + 1] for argv in calls if '--window' in argv}
            self.assertEqual(used, {expected}, windows)

    def test_board_reads_isuscope_without_the_ai_output_cap(self):
        seen = []
        def fake_run(argv, **kwargs):
            if argv[0] == 'isuscope':
                seen.append(kwargs.get('env') or {})
            class Done: returncode = 0; stdout = '{"run": "r", "summary": {"state": "complete"}}'; stderr = ''
            return Done()
        with patch.object(reads.subprocess, 'run', fake_run):
            board_data.detail(self.repo, self.cfg, 'r', '')
            reads.command_json(self.repo, ['isuscope', 'list'])
        self.assertTrue(all(env.get('ISUSCOPE_OUTPUT_BYTES') == '0' for env in seen[:-1]))
        self.assertNotIn('ISUSCOPE_OUTPUT_BYTES', seen[-1])

    def test_board_puts_shared_values_back_on_each_row(self):
        plain = board_data.with_common({'common': {'node': 'app1', 'labels': {'collector': 'alp'}},
                                        'rows': [{'route': '/a', 'labels': {'quantile': '0.95'}}]})
        self.assertNotIn('common', plain)
        self.assertEqual(plain['rows'][0], {'route': '/a', 'node': 'app1',
                                            'labels': {'collector': 'alp', 'quantile': '0.95'}})
        diff = board_data.with_common({'common': {'node': 'app1'},
                                       'rows': [{'key': {'digest': 'select 1'}, 'base': {'calls': 1}}]})
        self.assertEqual(diff['rows'][0]['key'], {'digest': 'select 1', 'node': 'app1'})
        self.assertEqual(board_data.with_common({'error': 'x'}), {'error': 'x'})

    def test_board_reads_tables_back_as_named_rows(self):
        table = reads.records({'total_count': 1, 'columns': ['node', 'calls'], 'rows': [['app1', 3]],
                                'hosts': {'columns': ['node'], 'rows': [['app1'], ['app2']]}})
        self.assertEqual(table['rows'], [{'node': 'app1', 'calls': 3}])
        self.assertEqual(table['hosts'], [{'node': 'app1'}, {'node': 'app2'}])
        self.assertNotIn('columns', table)
        diff = board_data.nested_comparison({'run': 'c', 'base': 'b', 'rows': [
            {'digest': 'select 1', 'presence': 'added', 'calls_base': None, 'calls': 3, 'calls_delta': None,
             'calls_delta_percent': None, 'p99_ms_base': None, 'p99_ms': 2}]})
        row = diff['rows'][0]
        self.assertEqual(row['key'], {'digest': 'select 1'})
        self.assertIsNone(row['base'])
        self.assertEqual(row['candidate'], {'calls': 3, 'p99_ms': 2})
        self.assertEqual(set(row['changes']), {'calls'})
        brief = board_data.brief_items({'http': {'total_count': 1, 'truncated': False, 'common': {'node': 'app1'},
                                                  'rows': [{'route': '/'}]}})
        self.assertEqual(brief['http']['items'], [{'route': '/', 'node': 'app1'}])
        self.assertNotIn('common', brief['http'])

    def test_auto_comparison_without_analysis_does_not_guess(self):
        def fake(repo, argv, **kwargs):
            if 'brief' in argv: return {'run': argv[2], 'summary': {'state': 'complete'}}
            return {'rows': []}
        with patch.object(board_data, 'command_json', fake):
            result = board_data.detail(self.repo, self.cfg, 'candidate', 'auto')
        self.assertIsNone(result['base'])

    def test_board_detail_resolves_ids_and_reads_every_part_once(self):
        calls = []
        def fake(repo, argv, **kwargs):
            calls.append((argv, kwargs))
            if 'sql' in argv: return {'rows': [{'id': 'initial-survey'}]}
            if 'series' in argv: return {'rows': [], 'window': 'load', 'truncated': False}
            if 'brief' in argv:
                return {'run': 'resolved-' + argv[2], 'summary': {'state': 'complete', 'score': 0},
                        'review': {'latest_analysis': {'body': 'evidence'}}}
            return {'rows': [{'presence': 'removed', 'base': {'total_ms': 12}, 'candidate': None}],
                    'total_count': 101, 'truncated': True}
        with patch.object(board_data, 'command_json', fake):
            data = board_data.detail(self.repo, self.cfg, 'candidate', 'baseline', 100)
        self.assertEqual(data['latest']['short_id'], 'resolved-candidate')
        self.assertEqual(data['base']['score'], 0)
        self.assertEqual(data['brief']['review']['latest_analysis']['body'], 'evidence')
        queries = [c for c, _ in calls if 'query' in c and '--base' in c]
        self.assertEqual(len(queries), 2)
        self.assertTrue(all(c[2] == 'resolved-candidate' and c[c.index('--base') + 1] == 'resolved-baseline'
                            and '--all-columns' in c for c in queries))
        self.assertIn('--window', queries[1])
        self.assertNotIn('--group-by', queries[1])
        self.assertTrue(data['sections']['sql']['data']['truncated'])
        self.assertIsNone(data['sections']['sql']['data']['rows'][0]['candidate'])
        self.assertTrue(all(options['max_bytes'] == 2_000_000 for _, options in calls))
        self.assertIsNone(self.cfg.get('base_run'))
        self.assertEqual(data['survey']['run'], 'resolved-initial-survey')
        self.assertIn(self.cfg['isuscope'] + ['query', 'resolved-candidate', '--view', 'http', '--limit', '500'], [c for c, _ in calls])
        self.assertIn(self.cfg['isuscope'] + ['query', 'initial-survey', '--metric-prefix', 'transition.', '--limit', '1000'], [c for c, _ in calls])
        series = [c for c, _ in calls if 'series' in c]
        self.assertEqual(len(series), 3)
        cpu = next(c for c in series if '--bucket' in c and c[c.index('--bucket') + 1] == '1')
        self.assertIn('host.cpu_busy_percent', cpu)
        self.assertTrue(all(c[2] == 'resolved-candidate' and '--limit' in c and '--window' in c for c in series))
        self.assertTrue(all(c[1] in ('brief', 'query', 'series', 'sql') for c, _ in calls))

    def test_board_detail_rejects_invalid_base_without_silent_fallback(self):
        def fake(repo, argv, **kwargs):
            if argv[2] == 'bad': return {'error': 'unknown run'}
            return {'run': 'ok', 'summary': {'state': 'complete'}}
        with patch.object(board_data, 'command_json', fake):
            self.assertEqual(board_data.detail(self.repo, self.cfg, 'ok', 'bad')['error'], 'unknown run')
        with patch.object(board_data, 'command_json', return_value={'run': 'active', 'summary': {'state': 'running'}}):
            self.assertIn('error', board_data.detail(self.repo, self.cfg, 'active'))

    def test_board_optional_measurements_fail_independently(self):
        def fake(repo, argv, **kwargs):
            if 'brief' in argv: return {'run': 'candidate', 'summary': {'state': 'complete'}}
            if 'series' in argv: return {'error': 'load window unavailable'}
            if 'sql' in argv: return {'rows': []}
            return {'rows': [], 'total_count': 0}
        with patch.object(board_data, 'command_json', fake):
            data = board_data.detail(self.repo, self.cfg, 'candidate')
        self.assertEqual(data['latest']['short_id'], 'candidate')
        self.assertNotIn('error', data['sections']['http']['data'])
        self.assertEqual(data['timeline']['error'], 'load window unavailable')
        self.assertIsNone(data['survey']['run'])

    def test_board_cpu_headline_is_the_p95_of_the_source_brief_prefers(self):
        def row(node, value, collector, metric='host.cpu_busy_percent'):
            return {'node': node, 'metric': metric, 'value': value, 'labels': {'collector': collector}}
        rows = [row('app1', v, 'host-sampler') for v in range(1, 101)]
        # sysstat and the total percent are fallbacks only, as in brief.
        rows += [row('app1', 100, 'sysstat'), row('app1', 100, 'host-sampler', 'host.cpu_percent')]
        rows += [row('app2', v, 'sysstat') for v in (10, 20)]
        result = board_data.cpu_p95({'window': 'load', 'truncated': False, 'rows': rows})
        self.assertEqual(result['nodes']['app1'], {'p95_percent': 95.05, 'samples': 100})
        self.assertEqual(result['nodes']['app2'], {'p95_percent': 19.5, 'samples': 2})
        self.assertEqual(board_data.cpu_p95({'error': 'busy'})['error'], 'busy')

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
                return {'run': argv[2], 'summary': {'state': 'complete'}, 'review': {'latest_analysis': dict(analysis)}}
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
        self.assertEqual(second['survey']['run'], 'survey')

    def test_board_detail_retries_measurements_that_failed(self):
        calls = []
        def fake(repo, argv, **kwargs):
            calls.append(argv)
            if 'brief' in argv: return {'run': 'candidate', 'summary': {'state': 'complete'}}
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
            if 'brief' in argv: return {'run': 'candidate', 'summary': {'state': 'complete'}}
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
            if 'brief' in argv: return {'run': 'candidate', 'summary': {'state': 'complete'}}
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

    def write_record(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def test_records_follow_the_fixed_list_and_show_what_is_missing(self):
        self.write_record('docs/benchmark-scenario.md', '# 見取り図\n')
        self.write_record('docs/official/manual.md', '# manual\n')
        self.git('add', '.')
        self.git('commit', '-m', 'Write the scenario')
        self.write_record('docs/state-log.md', '# 状態\n')
        listing = records.listing(self.repo)
        by_path = {record['path']: record for record in listing['records']}
        declared = [record['path'] for record in json.loads((SOURCE / 'config/records.json').read_text())['records']]
        self.assertEqual(list(by_path), declared)
        scenario = by_path['docs/benchmark-scenario.md']
        self.assertTrue(scenario['exists'])
        self.assertFalse(scenario['uncommitted'])
        self.assertEqual(scenario['last_commit']['subject'], 'Write the scenario')
        self.assertTrue(by_path['docs/state-log.md']['uncommitted'])
        self.assertIsNone(by_path['docs/state-log.md']['last_commit'])
        self.assertFalse(by_path['docs/cache-design.md']['exists'])
        official = by_path['docs/official/']
        self.assertEqual([f['path'] for f in official['files']], ['docs/official/manual.md'])

    def test_record_contents_are_limited_to_the_listed_places(self):
        self.write_record('docs/official/manual.md', 'manual text')
        self.write_record('docs/benchmark-scenario.md', 'scenario')
        self.write_record('AGENTS.md', 'not a record')
        self.assertEqual(records.content(self.repo, 'docs/benchmark-scenario.md')['text'], 'scenario')
        self.assertEqual(records.content(self.repo, 'docs/official/manual.md')['text'], 'manual text')
        self.assertIn('error', records.content(self.repo, 'docs/cache-design.md'))
        for path in ('AGENTS.md', 'docs/official/../../AGENTS.md', '/etc/passwd', 'docs/official/', ''):
            if path == 'docs/official/':
                self.assertIn('error', records.content(self.repo, path))
                continue
            with self.assertRaises(ValueError, msg=path):
                records.content(self.repo, path)
        outside = Path(self.temp.name) / 'secret.txt'
        outside.write_text('secret')
        (self.repo / 'docs/official/link.md').symlink_to(outside)
        with self.assertRaises(ValueError):
            records.content(self.repo, 'docs/official/link.md')
        self.write_record('docs/official/image.png', '')
        (self.repo / 'docs/official/image.png').write_bytes(b'\x89PNG\0\0')
        self.assertIn('error', records.content(self.repo, 'docs/official/image.png'))

    def test_queue_lists_measuring_next_waiting_and_held_in_that_order(self):
        import proposals
        stored = self.repo / '.local/proposals'
        stored.mkdir(parents=True)
        for branch, status, at in [('improve-held', 'held', '1'), ('improve-wait', 'delivered', '3'),
                                   ('human', 'submitted', '2'), ('improve-next', 'merged', '4'),
                                   ('improve-now', 'measuring', '5'), ('improve-done', 'accepted', '0')]:
            (stored / f'{branch}.json').write_text(json.dumps({
                'branch': branch, 'status': status, 'commit': '0123456789', 'first_submitted_at': at,
                'hypothesis': branch, 'author': {'role': 'improve' if branch != 'human' else 'worktree'},
                **({'hold': {'until': 'DBが最大の制約になったら'}} if status == 'held' else {})}))
        result = proposals.queue(self.repo)
        self.assertEqual([r['branch'] for r in result['items']],
                         ['improve-now', 'improve-next', 'human', 'improve-wait', 'improve-held'])
        # The queue counts what waits for a benchmark: the measured and the held are out of it.
        self.assertEqual(result['waiting'], 3)
        self.assertEqual(result['items'][-1]['hold']['until'], 'DBが最大の制約になったら')

    def test_http_board_read_only(self):
        p=subprocess.Popen([sys.executable,str(self.repo/'scripts/board/main.py'),'--port','0'],cwd=self.repo,stdout=subprocess.PIPE,text=True)
        try:
            url=p.stdout.readline().strip()
            with urllib.request.urlopen(url+'/api/board') as r:
                data=json.load(r)
                self.assertEqual(set(data),{'metrics','activity','queue'})
                self.assertEqual((data['queue']['waiting'],data['queue']['items']),(0,[]))
            with urllib.request.urlopen(url+'/api/records') as r:
                self.assertEqual(len(json.load(r)['records']), len(records.declared(SOURCE)))
            with urllib.request.urlopen(url+'/board-records.js') as r:
                self.assertIn('renderMarkdown', r.read().decode())
            with self.assertRaises(urllib.error.HTTPError) as outside:
                urllib.request.urlopen(url+'/api/record?path=AGENTS.md')
            self.assertEqual(outside.exception.code,400)
            outside.exception.close()
            with urllib.request.urlopen(url) as r:
                page=r.read().decode()
                self.assertIn('id="run-select"',page)
                self.assertIn('id="base-select"',page)
                self.assertLess(page.index('id="queue-section"'),page.index('id="overview"'))
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


@unittest.skipUnless(os.environ.get('ISUSCOPE_BOARD_REPO'), 'set ISUSCOPE_BOARD_REPO to a repository with finished runs')
class BoardReadsRealIsuscopeTests(unittest.TestCase):
    """The fakes above follow isuscope's output by hand; this reads a real run with the installed isuscope."""
    def test_board_reads_every_part_of_a_real_run(self):
        repo = Path(os.environ['ISUSCOPE_BOARD_REPO'])
        cfg = {'isuscope': ['isuscope']}
        metrics = reads.metrics(repo, cfg)
        self.assertNotIn('error', metrics)
        latest = metrics['latest']
        self.assertRegex(latest['short_id'], r'^[0-9a-f]{8}$')
        self.assertTrue(all('error' not in section['data'] for section in metrics['sections'].values()))
        data = board_data.detail(repo, cfg, latest['id'], 'auto', 25)
        self.assertNotIn('error', data)
        self.assertEqual(data['latest']['short_id'], latest['short_id'])
        brief = data['brief']
        self.assertIn('items', brief['hosts'])
        self.assertIn(brief['hosts']['window'], ('whole', 'load'))
        analysis = (brief.get('review') or {}).get('latest_analysis') or {}
        if analysis.get('full_text'):
            self.assertNotIn('…', analysis['body'][-1:])
        parts = [data['graph_http'], data['timeline'], data['mysql'],
                 *(section['data'] for section in data['sections'].values())]
        self.assertTrue(all('error' not in part for part in parts))
        self.assertIn('bucket_seconds', data['timeline']['range'])
        if data['base']:
            for section in data['sections'].values():
                for row in section['data']['rows']:
                    side = row['candidate'] or row['base']
                    self.assertIn('total_ms', side)
                    self.assertNotIn('total_ms', row['key'])


if __name__=='__main__': unittest.main()
