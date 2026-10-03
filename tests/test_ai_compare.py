"""Guard the reproducible task boundary without invoking AWS, models or Rust."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/ai-compare'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('ai_compare_manage', SCRIPTS / 'manage.py')
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)
from verify import build_environment


class TrialGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.dest = self.root / 'trial'
        (self.dest / '.local').mkdir(parents=True)
        (self.dest / '.local/environment.json').write_text('{}')
        (self.root / 'state/trials').mkdir(parents=True)
        self.state_patch = patch.object(manage, 'STATE', self.root / 'state')
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.manifest = {'trial': 'test-01', 'destination': str(self.dest),
                         'environment_file_sha256': manage.digest(self.dest / '.local/environment.json'),
                         'task_commit': 'initial', 'files': {},
                         'environment': {'build_env': {}, 'fingerprint': {}}}
        self.save_manifest()

    def save_manifest(self):
        manage.write_json(self.root / 'state/trials/test-01.json', self.manifest)

    def test_environment_change_is_rejected_using_controller_copy(self):
        (self.dest / '.local/manifest.json').write_text('{}')
        self.assertEqual(manage.trial_manifest(self.dest), self.manifest)
        (self.dest / '.local/environment.json').write_text('{"cargo": "wrong"}')
        with self.assertRaisesRegex(SystemExit, 'configuration changed'):
            manage.trial_manifest(self.dest)

    def test_duplicate_trial_refuses_before_touching_destination(self):
        args = argparse.Namespace(trial='test-01', destination=self.dest)
        with self.assertRaisesRegex(SystemExit, 'already registered'):
            manage.prepare(args)
        self.assertEqual((self.dest / '.local/environment.json').read_text(), '{}')

    def test_modified_verifier_is_not_executed_by_audit(self):
        (self.dest / 'verify.py').write_text('raise Exception("must not execute")')
        self.manifest['files'] = {'verify.py': 'original-sha'}
        self.save_manifest()
        with patch.object(manage, 'output', return_value='verify.py'), \
             patch.object(manage.subprocess, 'run') as run:
            with self.assertRaisesRegex(SystemExit, 'Protected'):
                manage.audit(argparse.Namespace(destination=self.dest))
            run.assert_not_called()

    def test_medium_acceptance_change_is_rejected_before_execution(self):
        source = self.dest / 'webapp/rust/src'
        source.mkdir(parents=True)
        acceptance = source / 'medium_acceptance.rs'
        acceptance.write_text('original')
        protected = 'webapp/rust/src/medium_acceptance.rs'
        self.manifest['files'] = {protected: manage.digest(acceptance)}
        self.manifest['protected_source'] = [protected]
        self.save_manifest()
        (source / 'new_user_cache_integration.rs').write_text('include!("medium_acceptance.rs");')
        acceptance.write_text('modified')
        with patch.object(manage, 'output', return_value=''), \
             patch.object(manage.subprocess, 'run') as run:
            with self.assertRaisesRegex(SystemExit, 'Protected'):
                manage.audit(argparse.Namespace(destination=self.dest))
            run.assert_not_called()

    def test_failed_child_records_failure_and_cannot_be_reused(self):
        code = 'import os; assert os.getcwd() == os.environ["PWD"]; assert "OLDPWD" not in os.environ; raise SystemExit(7)'
        args = argparse.Namespace(destination=self.dest, argv=[sys.executable, '-c', code],
                                  agent='test', model='none', endpoint='none', reasoning='none',
                                  harness='unittest', settings=[])
        def git_output(argv):
            return '' if 'status' in argv else 'initial'
        with patch.object(manage, 'output', side_effect=git_output), \
             patch.object(manage, 'fingerprint', return_value={}):
            with self.assertRaises(SystemExit) as failure:
                manage.run_trial(args)
            self.assertEqual(failure.exception.code, 7)
            record = json.loads((self.dest / '.local/run.json').read_text())
            self.assertEqual(record['exit_code'], 7)
            self.assertEqual(record['state'], 'exited')
            self.assertGreaterEqual(record['elapsed_seconds'], 0)
            with self.assertRaisesRegex(SystemExit, 'already started'):
                manage.run_trial(args)

    def test_rust_and_linker_overrides_do_not_leak_into_validation(self):
        with patch.dict(os.environ, {'RUSTFLAGS': '-Copt-level=3', 'RUSTC': '/wrong',
                                     'CARGO_TARGET_DIR': '/shared', 'CC': '/wrong'}):
            env = build_environment({'CC': '/fixed/clang', 'CARGO_BUILD_JOBS': '4'})
        self.assertNotIn('RUSTFLAGS', env)
        self.assertNotIn('RUSTC', env)
        self.assertNotIn('CARGO_TARGET_DIR', env)
        self.assertEqual(env['CC'], '/fixed/clang')


if __name__ == '__main__':
    unittest.main()
