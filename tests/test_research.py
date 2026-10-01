import concurrent.futures
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import test_operations
import research
import runner
import store


class ResearchTests(unittest.TestCase):
    setUp = test_operations.OperationsTests.setUp
    git = test_operations.OperationsTests.git
    query = test_operations.OperationsTests.query
    wait_for = test_operations.OperationsTests.wait_for

    def tearDown(self):
        research.stop_service(self.repo)
        test_operations.OperationsTests.tearDown(self)

    def proposal(self, name='p1', revision=1):
        return dict(proposal_id=name, revision=revision, question='遅延の原因は何か', summary='一覧SQLの改善仮説',
                    facts='loadでSQLが多い', hypothesis='N+1が原因の可能性', conditions='同じ結果を返すこと',
                    unknowns='ロック競合は不明', next_checks='呼び出し元を確認', code_commit=self.git('rev-parse','HEAD'),
                    run_ids=['run-12345678'], references=[dict(source='webapp/app.rs:10 @対象commit / run-12345678', snapshot='観測したSQL数10。dirtyなし')])

    def test_request_cli_accepts_natural_language_without_a_file(self):
        research.stop_service(self.repo)  # CLI引数の検証だけ。外部モデルは起動しない。
        cli = [sys.executable, str(self.repo/'scripts/operations/research.py'), 'request']
        question = '一覧APIが遅い原因を調べて。\n直近runとコードを照合してほしい'
        result = subprocess.run(cli+[question], cwd=self.repo, text=True, capture_output=True, check=True)
        job_id = json.loads(result.stdout)
        job = self.query('SELECT * FROM research_jobs WHERE job_id=?', (job_id,))[0]
        self.assertEqual(job['input'], question)
        self.assertEqual(job['state'], 'pending')
        result = subprocess.run(cli+['-'], input=question, cwd=self.repo, text=True, capture_output=True, check=True)
        self.assertEqual(self.query('SELECT input FROM research_jobs WHERE job_id=?', (json.loads(result.stdout),))[0]['input'], question)
        saved = self.repo/'question.txt'
        saved.write_text(question)
        result = subprocess.run(cli+['--file', str(saved)], cwd=self.repo, text=True, capture_output=True, check=True)
        self.assertEqual(self.query('SELECT input FROM research_jobs WHERE job_id=?', (json.loads(result.stdout),))[0]['input'], question)

    def setup_fake(self, delay=0, failures=()):
        self.cli.write_text('''import hashlib,json,os,sys,time
from pathlib import Path
text=sys.stdin.read()
kind=os.environ['SCAFFOLD_AGENT']
Path('.local/started-'+kind).write_text(json.dumps(dict(role=os.environ['SCAFFOLD_ROLE'],text=text,pid=os.getpid(),env=dict(os.environ))))
delay='''+repr(delay)+'''
time.sleep(delay.get(kind,0) if isinstance(delay,dict) else delay)
if kind in '''+repr(failures)+''': sys.exit(7)
if os.environ['SCAFFOLD_ROLE']=='researcher':
 print('''+repr(json.dumps(self.proposal('auto')))+''')
else:
 digest=text.split('対象proposal_sha256: ')[1].splitlines()[0]
 print(json.dumps(dict(summary='判断を変える新情報なし',details='指定commitとrunを照合。未確認点は残る',proposal_sha256=digest)))
''')
        for spec in [self.cfg['research']['researcher'], *self.cfg['research']['reviewers'].values()]:
            spec.update(model='fake', argv=[sys.executable, str(self.cli)], output='text')
        self.cfg['research']['timeout_seconds']=4
        (self.repo/'config/operations.json').write_text(json.dumps(self.cfg))

    def reviews(self, name='p1', revision=1):
        return self.query('SELECT * FROM research_jobs WHERE proposal_id=? AND revision=? ORDER BY kind', (name,revision))

    def test_publication_is_available_before_reviews_and_is_immutable(self):
        research.publish(self.repo,self.proposal())
        self.assertEqual([r['state'] for r in self.reviews()],['pending','pending'])
        path=self.repo/'docs/research/proposals/p1/1/proposal.json'
        self.assertEqual(json.loads(path.read_text()),self.proposal())
        self.assertIn('未レビュー',(self.repo/'docs/scout-board.md').read_text())
        research.publish(self.repo,self.proposal())
        self.assertEqual(len(self.reviews()),2)
        changed=self.proposal();changed['summary']='changed'
        with self.assertRaisesRegex(ValueError,'revision'):
            research.publish(self.repo,changed)
        research.publish(self.repo,self.proposal(revision=2))
        self.assertEqual(len(self.query('SELECT * FROM research_jobs')),4)
        self.assertEqual(json.loads(path.read_text())['summary'],'一覧SQLの改善仮説')

    def test_parallel_reviews_and_operator_records_are_unblocked(self):
        self.setup_fake(delay=.7)
        research.publish(self.repo,self.proposal())
        with patch.dict(os.environ,{'SCAFFOLD_SESSION_ID':'operator','CODEX_THREAD_ID':'operator','SCAFFOLD_TASK_ID':'worker','SCAFFOLD_AGENT':'wrong'}):
            research.start(self.repo)
        self.wait_for(lambda: all((self.repo/('.local/started-'+k)).exists() for k in ('codex','claude')))
        self.assertTrue(all(r['state']=='running' for r in self.reviews()))
        with store.connect(self.repo) as db:
            db.execute("INSERT INTO operators(agent,session_id) VALUES('codex','operator')")
        self.assertFalse((self.repo/'.local/operation.lock').exists())
        self.wait_for(lambda: all(r['state']=='complete' for r in self.reviews()))
        for row in self.reviews():
            record=json.loads((self.repo/('.local/started-'+row['kind'])).read_text())
            self.assertEqual(record['role'],'reviewer')
            self.assertNotIn('CODEX_THREAD_ID',record['env'])
            self.assertNotIn('SCAFFOLD_SESSION_ID',record['env'])
            self.assertIn('run-12345678',record['text'])
            self.assertIn('遅延の原因は何か',record['text'])
            self.assertIn('ベンチマーカー内部',record['text'])
        self.assertNotEqual(*[r['attempt_id'] for r in self.reviews()])
        self.wait_for(lambda: len(list((self.repo/'docs/research/proposals/p1/1').glob('*.json')))==3)

    def test_one_failure_retry_keeps_other_result_and_history(self):
        self.setup_fake(failures=('claude',))
        research.publish(self.repo,self.proposal())
        research.start(self.repo)
        self.wait_for(lambda: {r['state'] for r in self.reviews()}=={'complete','failed'})
        good=next(r for r in self.reviews() if r['state']=='complete')
        bad=next(r for r in self.reviews() if r['state']=='failed')
        self.assertIn('exit 7',bad['error'])
        research.stop_service(self.repo)
        self.setup_fake()
        research.retry(self.repo,bad['job_id'])
        research.start(self.repo,enable=True)
        self.wait_for(lambda: all(r['state']=='complete' for r in self.reviews()))
        self.assertEqual(next(r for r in self.reviews() if r['kind']==good['kind'])['attempt_id'],good['attempt_id'])
        self.assertEqual(len(self.query('SELECT * FROM research_attempts')),3)
        with self.assertRaises(ValueError):research.retry(self.repo,good['job_id'])

    def test_both_failures_and_invalid_output_preserve_proposal(self):
        self.setup_fake(failures=('codex','claude'))
        research.publish(self.repo,self.proposal())
        research.start(self.repo)
        self.wait_for(lambda: all(r['state']=='failed' for r in self.reviews()))
        self.assertEqual(len(research.snapshot(self.repo)['proposals']),1)
        research.stop_service(self.repo)
        self.cli.write_text('print("{}")')
        for j in self.reviews():
            research.retry(self.repo,j['job_id'])
            self.assertFalse(research.run_job(self.repo,j['job_id'],self.cfg,threading.Event()))
        self.assertTrue(all(r['result'] is None for r in self.reviews()))

    def test_stop_restart_pending_and_explicit_retry(self):
        self.setup_fake(delay=2)
        research.publish(self.repo,self.proposal())
        research.start(self.repo)
        self.wait_for(lambda: all((self.repo/('.local/started-'+k)).exists() for k in ('codex','claude')))
        research.stop_service(self.repo)
        self.assertTrue(all(r['state']=='failed' for r in self.reviews()))
        research.publish(self.repo,self.proposal('p2'))
        research.start(self.repo)
        self.assertFalse(research.snapshot(self.repo)['running'])
        self.setup_fake()
        research.start(self.repo,enable=True)
        self.wait_for(lambda: all(r['state']=='complete' for r in self.reviews('p2')))
        self.assertTrue(all(r['state']=='failed' for r in self.reviews()))
        self.assertEqual(len(research.snapshot(self.repo)['proposals']),2)

    def test_crash_recovery_and_duplicate_start_do_not_duplicate_jobs(self):
        self.setup_fake(delay=2)
        research.publish(self.repo,self.proposal())
        research.start(self.repo);research.start(self.repo)
        self.wait_for(lambda: all((self.repo/('.local/started-'+k)).exists() for k in ('codex','claude')))
        s=self.query('SELECT * FROM research_service')[0]
        os.kill(s['pid'],signal.SIGKILL)
        self.wait_for(lambda: not research.snapshot(self.repo)['running'])
        self.assertTrue(all(r['state']=='failed' for r in research.snapshot(self.repo)['proposals'][0]['reviews']))
        research.recover(self.repo)
        self.assertTrue(all(r['state']=='failed' for r in self.reviews()))
        self.assertEqual(len(self.query('SELECT * FROM research_attempts')),2)
        children=[json.loads((self.repo/('.local/started-'+k)).read_text())['pid'] for k in ('codex','claude')]
        self.wait_for(lambda: all(research.birth(pid) is None for pid in children))
        self.setup_fake()
        for r in self.reviews(): research.retry(self.repo,r['job_id'])
        research.start(self.repo)
        self.wait_for(lambda: all(r['state']=='complete' for r in self.reviews()))
        self.assertEqual(len(self.query('SELECT * FROM research_attempts')),4)

    def test_researcher_output_publishes_and_reviews_original_question(self):
        self.setup_fake()
        job=research.request(self.repo,'operatorの元の問い')
        research.start(self.repo)
        self.wait_for(lambda: len(self.reviews('auto'))==2 and all(r['state']=='complete' for r in self.reviews('auto')))
        proposal=json.loads(self.query('SELECT body FROM research_proposals')[0]['body'])
        self.assertEqual(proposal['question'],'operatorの元の問い')
        self.assertEqual(self.query('SELECT state FROM research_jobs WHERE job_id=?',(job,))[0]['state'],'complete')
        self.assertEqual(self.query('SELECT * FROM workers'),[])
        self.assertEqual(self.query('SELECT * FROM operators'),[])

    def test_atomic_claim_wrong_hash_timeout_and_missing_artifact(self):
        self.setup_fake(delay=.3)
        research.publish(self.repo,self.proposal())
        job=self.reviews()[0]['job_id']
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results=list(pool.map(lambda _:research.run_job(self.repo,job,self.cfg,threading.Event()),range(2)))
        self.assertEqual(sorted(results),[False,True])
        self.assertEqual(len(self.query('SELECT * FROM research_attempts')),1)
        job=self.reviews()[1]['job_id']
        self.cli.write_text('import json\nprint(json.dumps(dict(summary="ok",details="ok",proposal_sha256="wrong")))')
        self.assertFalse(research.run_job(self.repo,job,self.cfg,threading.Event()))
        self.assertIn('SHA256',self.query('SELECT error FROM research_jobs WHERE job_id=?',(job,))[0]['error'])
        research.retry(self.repo,job)
        self.cli.write_text('import time\ntime.sleep(20)')
        self.cfg['research']['timeout_seconds']=.15
        self.assertFalse(research.run_job(self.repo,job,self.cfg,threading.Event()))
        research.retry(self.repo,job)
        self.cfg['research']['reviewers']['codex']['output']='file'
        self.cli.write_text('print("exit zero without file")')
        self.assertFalse(research.run_job(self.repo,job,self.cfg,threading.Event()))
        self.assertEqual(self.query('SELECT state FROM research_jobs WHERE job_id=?',(job,))[0]['state'],'failed')


    def test_delayed_side_does_not_hide_arrived_review(self):
        self.setup_fake(delay={'codex':0,'claude':2})
        research.publish(self.repo,self.proposal())
        research.start(self.repo)
        self.wait_for(lambda: {r['state'] for r in self.reviews()}=={'complete','running'})
        self.wait_for(lambda: len(list((self.repo/'docs/research/proposals/p1/1').glob('codex-*.json')))==1)
        data=research.snapshot(self.repo)['proposals'][0]
        self.assertTrue(next(j for j in data['reviews'] if j['kind']=='codex')['result'])
        artifact=next((self.repo/'docs/research/proposals/p1/1').glob('codex-*.json'))
        self.assertEqual(next(iter(json.loads(artifact.read_text()))),'summary')
        self.assertIn('判断を変える新情報なし',(self.repo/'docs/scout-board.md').read_text())
        self.assertIsNone(next(j for j in data['reviews'] if j['kind']=='claude')['result'])

    def test_export_failure_and_launch_failure_cannot_undo_publication(self):
        with patch.object(research,'refresh_board',side_effect=OSError('disk export failure')):
            research.publish(self.repo,self.proposal())
        self.assertEqual(len(research.snapshot(self.repo)['proposals']),1)
        research.refresh_board(self.repo)
        with patch.object(research.subprocess,'Popen',side_effect=OSError('launch failed')):
            # Avoid patching ps too: start's service row has no process yet.
            with self.assertRaises(OSError): research.start(self.repo)
        self.assertEqual([r['state'] for r in self.reviews()],['pending','pending'])
        self.assertTrue((self.repo/'docs/research/proposals/p1/1/proposal.json').exists())

    def test_http_board_exposes_proposal_and_review_without_mutations(self):
        import urllib.request
        self.setup_fake()
        research.publish(self.repo,self.proposal())
        for j in self.reviews(): research.run_job(self.repo,j['job_id'],self.cfg,threading.Event())
        p=subprocess.Popen([sys.executable,str(self.repo/'scripts/operations/main.py'),'board','--port','0'],cwd=self.repo,stdout=subprocess.PIPE,text=True)
        try:
            url=p.stdout.readline().strip()
            with urllib.request.urlopen(url+'/api/board') as response:
                data=json.load(response)
            self.assertEqual(len(data['scouts']),4)
            self.assertEqual(len(data['research']['proposals']),1)
            self.assertTrue(all(j['state']=='complete' for j in data['research']['proposals'][0]['reviews']))
        finally:
            p.terminate();p.wait(timeout=5);p.stdout.close()


if __name__=='__main__': unittest.main()
