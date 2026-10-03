import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts/operations'))
import store
import handoffs


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo';self.repo.mkdir()
        self.git('init','-b','main');self.git('config','user.email','fixture@example.invalid');self.git('config','user.name','fixture')
        (self.repo/'.gitignore').write_text('.local/\n')
        (self.repo/'webapp/sql').mkdir(parents=True);(self.repo/'config').mkdir()
        (self.repo/'config/sync.json').write_text(json.dumps({'items':[],'post_deploy_commands':[]}))
        self.git('add','.');self.git('commit','-qm','base')
        self.worker=Path(self.tmp.name)/'worker';self.git('worktree','add','-b','task',str(self.worker))
        self.env={'SCAFFOLD_SESSION_ID':'worker','SCAFFOLD_PARENT_SESSION_ID':'operator','SCAFFOLD_AGENT':'codex','SCAFFOLD_ROLE':'worker'}
        self.sql("INSERT INTO worker_start VALUES ('fixture',5,'same results','test');")
        with store.connect(self.repo) as db:self.task=db.execute('SELECT task_id FROM workers').fetchone()[0]

    def git(self,*args,cwd=None):
        return subprocess.check_output(['git','-C',str(cwd or self.repo),*args],text=True,stderr=subprocess.DEVNULL).strip()

    def sql(self,sql,operator=False):
        env=dict(self.env)
        if operator:env.update(SCAFFOLD_ROLE='operator',SCAFFOLD_SESSION_ID='operator')
        with patch.dict(os.environ,env):return store.worker_sql(self.repo,self.repo if operator else self.worker,sql)

    def change(self):
        (self.worker/'webapp/sql').mkdir(parents=True,exist_ok=True)
        (self.worker/'webapp/sql/restore.sql').write_text('SELECT 1;')
        self.git('add','.',cwd=self.worker);self.git('commit','-qm','restore',cwd=self.worker)

    def publish(self,requirements):
        return self.sql("INSERT INTO worker_handoff VALUES ('fixture passed','none','"+json.dumps(requirements)+"');")

    def test_missing_requirement_rolls_back_and_records_checkpoint_without_completing(self):
        self.change()
        with self.assertRaisesRegex(ValueError,'declare deployment'):
            self.publish([])
        with store.connect(self.repo) as db:self.assertEqual(db.execute('SELECT count(*) FROM worker_handoffs').fetchone()[0],0)
        req=[{'path':'webapp/sql/restore.sql','node_group':'role_app','remote':'/app/sql/restore.sql'}]
        row=self.publish(req)[0]
        self.assertEqual(row['state'],'working');self.assertEqual(row['result_commit'],self.git('rev-parse','HEAD',cwd=self.worker))
        hid=row['handoffs'][0]['handoff_id']
        with self.assertRaisesRegex(ValueError,'not fully integrated'):
            self.sql(f'INSERT INTO worker_integrate VALUES ({hid});',operator=True)
        self.git('merge','--ff-only','task')
        with self.assertRaisesRegex(ValueError,'mapping'):
            self.sql(f'INSERT INTO worker_integrate VALUES ({hid});',operator=True)
        (self.repo/'config/sync.json').write_text(json.dumps({'items':[{'type':'file','local':req[0]['path'],'remote':req[0]['remote'],'node_group':'role_app'}]}))
        self.git('add','.');self.git('commit','-qm','mapping')
        self.sql(f'INSERT INTO worker_integrate VALUES ({hid});',operator=True)
        with store.connect(self.repo) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM worker_integrations').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT state FROM workers').fetchone()[0],'working')

    def test_stop_requires_ack_and_cli_exit(self):
        with store.connect(self.repo) as db:db.execute('INSERT INTO worker_processes(process_id,task_id,session_id,pid) VALUES (?,?,?,?)',('p',self.task,'worker',123))
        self.sql("INSERT INTO worker_stop VALUES ('"+self.task+"','user ended practice');",operator=True)
        def status():
            with store.connect(self.repo) as db:return db.execute('SELECT status FROM worker_stops').fetchone()[0]
        self.assertEqual(status(),'requested')
        with self.assertRaises(sqlite3.IntegrityError):self.sql("INSERT INTO worker_stop_ack VALUES (1,'saved');",operator=True)
        self.sql("INSERT INTO worker_stop_ack VALUES (1,'uncommitted work preserved; exiting CLI');")
        self.assertEqual(status(),'acknowledged')
        with store.connect(self.repo) as db:db.execute("UPDATE worker_processes SET exited_at=strftime('%s','now'),exit_code=0 WHERE process_id='p'")
        self.assertEqual(status(),'stopped')
        self.change()
        with self.assertRaisesRegex(sqlite3.IntegrityError,'acknowledged stop'):
            self.publish([])

    def test_manifest_checks_group_path_and_migration(self):
        req=[{'path':'webapp/sql/migrate.sql','remote':'/app/migrate.sql','node_group':'db','command':'run migration'}]
        manifest={'items':[{'type':'directory','local':'webapp/sql','remote':'/app','node_group':'app'}]}
        with self.assertRaisesRegex(ValueError,'mapping'):handoffs.check_manifest(req,manifest)
        manifest['items'][0]['node_group']='db'
        with self.assertRaisesRegex(ValueError,'migration'):handoffs.check_manifest(req,manifest)
        manifest['post_deploy_commands']=[{'node_group':'db','command':'run migration'}]
        handoffs.check_manifest(req,manifest)

    def test_launcher_waits_for_ack_before_terminating_child(self):
        spec=importlib.util.spec_from_file_location('worker_launcher',ROOT/'scripts/worker-iterm.py')
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        with store.connect(self.repo) as db:
            db.execute('INSERT INTO worker_processes(process_id,task_id,session_id,pid) VALUES (?,?,?,?)',('p',self.task,'worker',123))
        self.sql("INSERT INTO worker_stop VALUES ('"+self.task+"','stop');",operator=True)
        child=Mock();child.wait.side_effect=[subprocess.TimeoutExpired('fixture',1),0]
        self.assertEqual(worker.wait_for_worker(self.repo,'p',child),0);child.terminate.assert_not_called()
        self.sql("INSERT INTO worker_stop_ack VALUES (1,'saved');")
        child=Mock();child.wait.side_effect=[subprocess.TimeoutExpired('fixture',1),-15]
        self.assertEqual(worker.wait_for_worker(self.repo,'p',child),-15);child.terminate.assert_called_once()
