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
    def exercise(self):
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
            for name, event in [('bootstrap.sh','bootstrap'), ('inspect-environment.sh','inspect'), ('configure-draft.sh','draft')]:
                (root/'scripts'/name).write_text('#!/bin/sh\necho '+event+' >> events\n')
            for p in (root/'scripts').glob('*.sh'):p.chmod(0o755)
            (root/'scripts/phase1-build.py').write_text('import sys\nopen("events","a").write("build-"+sys.argv[1]+"\\n")\n')
            (root/'scripts/review-draft.py').write_text('')
            result=subprocess.run(['bash','scripts/kickoff.sh'],cwd=root,env=dict(os.environ,ISUSCOPE_LOCK_HELD='1'),capture_output=True,text=True)
            self.assertEqual((root/'events').read_text().splitlines(), ['build-start','bootstrap','inspect','draft','build-status'])
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_build_starts_before_bootstrap_without_another_session(self):
        self.exercise()


if __name__ == '__main__':
    unittest.main()
