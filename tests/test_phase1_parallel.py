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
            # The time spent waiting for settings is recorded beside kickoff's stages.
            rows = [line.split('\t') for line in (root/'.local/stage-timing.tsv').read_text().splitlines()]
            self.assertEqual([row[2] for row in rows], ['inspecting', 'needs_configuration', 'building', 'complete'])
            self.assertTrue(all(row[1] == 'phase1-build' and row[4] == '0' for row in rows))

    def test_probe_failure_never_compiles(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.multiple(build, ROOT=root, STATE=root/'state', CONFIG=root/'config.json'), patch.object(build.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'SSH failed')) as run:
                with self.assertRaises(RuntimeError):
                    build.run('app1', 'webapp/rust')
                self.assertEqual(run.call_count, 1)
                self.assertEqual(json.loads((root/'state/status.json').read_text())['state'], 'failed')


class ProposalTests(unittest.TestCase):
    """Settings the build lane writes by itself, and the cases it leaves to the operator."""

    def proposal(self, environment, cargo_toml='[package]\nname = "isuride"\n', packages=('serde',)):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            app = root / 'webapp/rust'
            app.mkdir(parents=True)
            (app / 'Cargo.toml').write_text(cargo_toml)
            (app / 'Cargo.lock').write_text(''.join(f'[[package]]\nname = "{name}"\nversion = "1.0.0"\n\n' for name in packages))
            (root / 'config/rust-builder').mkdir(parents=True)
            shutil.copy(ROOT / 'config/rust-builder/bases.json', root / 'config/rust-builder/bases.json')
            with patch.multiple(build, ROOT=root, BASES=root / 'config/rust-builder/bases.json'):
                return build.propose(environment, 'webapp/rust')

    def test_newest_base_not_newer_than_the_node_glibc(self):
        settings, _ = self.proposal('x86_64\nNAME="Ubuntu"\nglibc 2.39\n')
        self.assertEqual(settings, {'base_image': 'rust:1-bookworm', 'target': 'x86_64-unknown-linux-gnu',
                                    'binary': 'isuride', 'dockerfile': 'config/rust-builder/Dockerfile'})
        settings, _ = self.proposal('aarch64\nglibc 2.35\n')
        self.assertEqual((settings['base_image'], settings['target']), ('rust:1-bullseye', 'aarch64-unknown-linux-gnu'))

    def test_openssl_needs_the_same_libssl_major(self):
        settings, _ = self.proposal('x86_64\nglibc 2.39\nOpenSSL 3.0.13 30 Jan 2024\n', packages=('openssl-sys',))
        self.assertEqual(settings['base_image'], 'rust:1-bookworm')
        # Ubuntu 22.04: glibc 2.35 with OpenSSL 3; bullseye has 1.1 and bookworm's glibc is too new.
        settings, reasons = self.proposal('x86_64\nglibc 2.35\nOpenSSL 3.0.2 15 Mar 2022\n', packages=('openssl-sys',))
        self.assertIsNone(settings)
        self.assertIn('libssl 3', reasons[0])

    def test_unclear_cases_wait_for_the_operator(self):
        for kwargs in [dict(cargo_toml='[workspace]\nmembers = ["app"]\n'),
                       dict(cargo_toml='[package]\nname = "a"\n[[bin]]\nname = "a"\n[[bin]]\nname = "b"\n'),
                       dict(packages=('mysqlclient-sys',))]:
            with self.subTest(**kwargs):
                settings, reasons = self.proposal('x86_64\nglibc 2.39\n', **kwargs)
                self.assertIsNone(settings)
                self.assertTrue(reasons)
        self.assertIsNone(self.proposal('riscv64\nglibc 2.39\n')[0])
        self.assertIsNone(self.proposal('x86_64\nglibc 2.28\n')[0])

    def test_proposal_is_written_and_built_without_waiting(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            app = root / 'webapp/rust'
            app.mkdir(parents=True)
            (app / 'Cargo.toml').write_text('[package]\nname = "isuride"\n')
            (app / 'Cargo.lock').write_text('')
            (root / 'config/rust-builder').mkdir(parents=True)
            shutil.copy(ROOT / 'config/rust-builder/bases.json', root / 'config/rust-builder/bases.json')
            calls = []
            def run(args, **kwargs):
                calls.append(args)
                return subprocess.CompletedProcess(args, 0, 'x86_64\nglibc 2.36\n', '')
            with patch.multiple(build, ROOT=root, STATE=root/'state', CONFIG=root/'config/phase1-build.json',
                                BASES=root/'config/rust-builder/bases.json'), \
                    patch.object(build.subprocess, 'run', run), patch.object(build.time, 'sleep', side_effect=AssertionError('waited')):
                build.run('app1', 'webapp/rust')
            self.assertEqual(json.loads((root/'config/phase1-build.json').read_text())['base_image'], 'rust:1-bookworm')
            states = [line.split('\t')[2] for line in (root/'.local/stage-timing.tsv').read_text().splitlines()]
            self.assertEqual(states, ['inspecting', 'configured', 'building', 'complete'])
            # The agent following progress.log learns the settings were written for it to review.
            log = (root/'.local/progress.log').read_text()
            self.assertIn('phase1-build\tconfigured', log)
            self.assertIn('phase1-build\tcomplete', log)

    def test_auto_configuration_can_be_turned_off(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'webapp/rust').mkdir(parents=True)
            (root / 'webapp/rust/Cargo.toml').write_text('[package]\nname = "isuride"\n')
            (root / 'webapp/rust/Cargo.lock').write_text('')
            def run(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, 'x86_64\nglibc 2.36\n', '')
            with patch.multiple(build, ROOT=root, STATE=root/'state', CONFIG=root/'config.json'), \
                    patch.dict(os.environ, {'PHASE1_BUILD_AUTO_CONFIG': '0'}), \
                    patch.object(build.subprocess, 'run', run), patch.object(build.time, 'sleep', side_effect=RuntimeError('stop')), \
                    patch.object(build.time, 'monotonic', return_value=0):
                with self.assertRaises(RuntimeError):
                    build.run('app1', 'webapp/rust')
            self.assertFalse((root/'config.json').exists())
            self.assertIn('settings', json.loads((root/'state/proposal.json').read_text()))


class KickoffTests(unittest.TestCase):
    def exercise(self):
        with tempfile.TemporaryDirectory(prefix='phase1 space ') as d:
            root = Path(d)
            for folder in ('scripts', 'config', 'webapp/rust', 'webapp/sql', '.local'):
                (root/folder).mkdir(parents=True)
            for name in ('kickoff.sh', 'application-policy.sh', 'stage-lib.sh', 'progress.py'):
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
            (root/'scripts/lane.py').write_text('import sys\nopen("events","a").write("lanes-"+sys.argv[1]+"\\n")\n')
            (root/'scripts/environment-report.py').write_text('open("events","a").write("environment\\n")\n')
            (root/'scripts/review-draft.py').write_text('')
            result=subprocess.run(['bash','scripts/kickoff.sh'],cwd=root,env=dict(os.environ,ISUSCOPE_LOCK_HELD='1'),capture_output=True,text=True)
            self.assertEqual((root/'events').read_text().splitlines(),
                             ['build-start','lanes-kickoff','bootstrap','inspect','environment','draft','build-status'])
            self.assertEqual(result.returncode, 0, result.stderr)
            # Nested steps keep their own timing although the outer lock records only kickoff.
            stages = [line.split('\t')[1:3] for line in (root/'.local/stage-timing.tsv').read_text().splitlines()]
            self.assertEqual(stages, [['kickoff.sh', name] for name in
                                      ('build-dispatch', 'lanes', 'bootstrap', 'inspect', 'environment-report',
                                       'configure-draft', 'review-draft')])
            # The agent following progress.log sees when code can be read and when the draft is ready.
            states = [line.split('\t')[1:3] for line in (root/'.local/progress.log').read_text().splitlines()]
            self.assertEqual(states, [['kickoff', 'started'], ['kickoff', 'code-ready'], ['kickoff', 'draft-ready']])

    def test_build_starts_before_bootstrap_without_another_session(self):
        self.exercise()


if __name__ == '__main__':
    unittest.main()
