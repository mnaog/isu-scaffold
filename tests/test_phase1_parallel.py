import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('phase1_build', ROOT / 'scripts/phase1-build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class BuildLaneTests(unittest.TestCase):
    def test_configuration_arrival_starts_build_without_second_command(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            config = root / 'config.json'
            settings = dict(base_image='rust:1.80-bullseye', target='x86_64-unknown-linux-gnu',
                            binary='fixture', dockerfile='config/rust-builder/Dockerfile')
            def supply(_):
                self.assertEqual(json.loads((root/'state/status.json').read_text())['state'], 'needs_configuration')
                config.write_text(json.dumps(settings))
            calls = []
            def run(args, **kwargs):
                calls.append(args)
                return subprocess.CompletedProcess(args, 0, 'x86_64\nfixture OS', '')
            with patch.multiple(build, ROOT=root, STATE=root/'state', CONFIG=config), patch.object(build.subprocess, 'run', run), patch.object(build.time, 'sleep', supply):
                build.run('app1', 'webapp/rust')
            self.assertEqual(json.loads((root/'state/status.json').read_text())['state'], 'complete')
            self.assertEqual([c[2] for c in calls[1:]], ['validate', 'build'])
            manifest = json.loads((root/'state/manifest.json').read_text())
            self.assertEqual(manifest['local_builds'][0]['base_image'], settings['base_image'])

    def test_probe_failure_never_compiles(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.multiple(build, ROOT=root, STATE=root/'state', CONFIG=root/'config.json'), patch.object(build.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'SSH failed')) as run:
                with self.assertRaises(RuntimeError):
                    build.run('app1', 'webapp/rust')
                self.assertEqual(run.call_count, 1)
                self.assertEqual(json.loads((root/'state/status.json').read_text())['state'], 'failed')


class KickoffTests(unittest.TestCase):
    def exercise(self, worker_exit):
        with tempfile.TemporaryDirectory(prefix='phase1 space ') as d:
            root = Path(d)
            for folder in ('scripts', 'config', 'webapp/rust', 'webapp/sql', '.local'):
                (root/folder).mkdir(parents=True)
            for name in ('kickoff.sh', 'application-policy.sh'):
                shutil.copy(ROOT/'scripts'/name, root/'scripts'/name)
            (root/'config/application.env').write_text('APPLICATION_LANGUAGE=rust\nAPPLICATION_PATH=webapp/rust\n')
            (root/'webapp/rust/Cargo.toml').write_text('[package]\nname="fixture"\n')
            (root/'.local/ansible-inventory.json').write_text(json.dumps({'all': {'children': {'application': {'hosts': {'app1': {}}}}}}))
            for args in (['init', '-q'], ['add', '.'], ['-c','user.name=test','-c','user.email=test@example.com','commit','-qm','fixture']):
                subprocess.run(['git','-C',d,*args],check=True)
            (root/'scripts/worktree.sh').write_text('#!/bin/sh\nprintf "worktree: %s/lane with spaces\\n" "$PWD"\n')
            for name, event in [('bootstrap.sh','bootstrap'), ('inspect-environment.sh','inspect'), ('configure-draft.sh','draft')]:
                (root/'scripts'/name).write_text('#!/bin/sh\necho '+event+' >> events\n')
            for p in (root/'scripts').glob('*.sh'):p.chmod(0o755)
            (root/'scripts/phase1-build.py').write_text('import sys\nopen("events","a").write("build-"+sys.argv[1]+"\\n")\n')
            (root/'scripts/worker-iterm.py').write_text('import sys\nassert sys.argv[1].endswith("lane with spaces")\nopen("events","a").write("worker\\n")\nsys.exit('+str(worker_exit)+')\n')
            (root/'scripts/review-draft.py').write_text('')
            result=subprocess.run(['bash','scripts/kickoff.sh'],cwd=root,env=dict(os.environ,ISUSCOPE_LOCK_HELD='1'),capture_output=True,text=True)
            expected = ['build-start','worker','bootstrap','inspect','draft']
            if worker_exit == 0:
                expected.append('worker')  # task registration checked after setup
            self.assertEqual((root/'events').read_text().splitlines(), expected + ['build-status'])
            self.assertEqual(result.returncode, 0 if worker_exit == 0 else 1, result.stderr)

    def test_both_lanes_dispatched_before_bootstrap(self):
        self.exercise(0)

    def test_worker_failure_reported_without_skipping_setup(self):
        self.exercise(2)

class WorkerTerminalTests(unittest.TestCase):
    def test_foreground_cli_clears_parent_identity_and_lock(self):
        spec = importlib.util.spec_from_file_location('worker_iterm', ROOT/'scripts/worker-iterm.py')
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            wt = repo/'lane'
            (wt/'.local').mkdir(parents=True)
            (wt/'.local/lane.md').write_text('fixture')
            from unittest.mock import Mock
            child = Mock(pid=12345)
            child.wait.return_value = 0
            def git(cwd, *args):
                if args[0] == 'branch': return 'worker'
                return 'task' if args[-1].endswith('scaffold-mode') else 'fixture purpose'
            with patch.object(worker, 'root', return_value=repo), patch.object(worker, 'git', git), patch.object(worker.shutil, 'which', return_value='/fixture/codex'), patch.object(worker.sys, 'argv', ['worker',str(wt),'--parent','actual-parent','--run']), patch.object(worker.sys.stdin, 'isatty', return_value=True), patch.object(worker.sys.stdout, 'isatty', return_value=True), patch.object(worker.signal, 'signal'), patch.object(worker.subprocess, 'Popen', return_value=child) as spawn, patch.dict(os.environ, {'CODEX_THREAD_ID':'parent','SCAFFOLD_SESSION_ID':'parent','ISUSCOPE_LOCK_HELD':'1'}):
                with self.assertRaises(SystemExit) as exit:
                    worker.main()
            self.assertEqual(exit.exception.code, 0)
            args, kwargs = spawn.call_args
            self.assertNotIn('exec', args[0])
            self.assertNotIn('stdout', kwargs)
            self.assertNotIn('start_new_session', kwargs)
            self.assertNotIn('CODEX_THREAD_ID', kwargs['env'])
            self.assertNotIn('ISUSCOPE_LOCK_HELD', kwargs['env'])
            self.assertEqual(kwargs['env']['SCAFFOLD_PARENT_SESSION_ID'], 'actual-parent')
            self.assertIn('actual-parent', args[0][-1])
            context = json.loads((wt/'.local/worker-context.json').read_text())
            self.assertEqual(context['parent_session_id'], 'actual-parent')
            self.assertEqual(context['purpose'], 'fixture purpose')

    def test_cli_running_is_not_task_registration(self):
        spec = importlib.util.spec_from_file_location('worker_wait', ROOT/'scripts/worker-iterm.py')
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            context = repo/'context.json'
            record = dict(state='running',process_id='process',pid=12345,worktree=str(repo/'lane'),parent_session_id='parent')
            context.write_text(json.dumps(record))
            with self.assertRaisesRegex(RuntimeError, 'task registration not confirmed'):
                worker.wait_started(repo, context, 0)
            with worker.connect(repo) as db:
                db.execute("INSERT INTO workers(task_id,task,estimate_minutes,completion_criteria,planned_validation,parent_session_id,session_id,agent,worktree,branch,base_commit) VALUES ('task','purpose',5,'done','test','parent','child','codex',?,'worker',?)", (record['worktree'],'a'*40))
                db.execute("INSERT INTO worker_processes(process_id,session_id,pid) VALUES ('process','child',12345)")
            with self.assertRaisesRegex(RuntimeError, 'task registration not confirmed'):
                worker.wait_started(repo, context, 0)
            with worker.connect(repo) as db:
                db.execute("UPDATE worker_processes SET task_id='task'")
            self.assertEqual(worker.wait_started(repo, context, 0)['task_id'], 'task')
            with worker.connect(repo) as db:
                db.execute("UPDATE worker_processes SET exited_at=1")
            with self.assertRaisesRegex(RuntimeError, 'task registration not confirmed'):
                worker.wait_started(repo, context, 0)


if __name__ == '__main__':
    unittest.main()
