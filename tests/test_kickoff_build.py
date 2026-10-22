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
spec = importlib.util.spec_from_file_location('kickoff_build', ROOT / 'scripts/kickoff-build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


GO_MOD = 'module github.com/example/isuride\n\ngo 1.23\n\nrequire (\n\tgithub.com/go-sql-driver/mysql v1.8.1\n)\n'
PROBE = 'x86_64\nNAME="Ubuntu"\nunit\tisuride-go.service\t/home/isucon/webapp/go/isuride\nunit\tisuride-matcher.service\t/usr/bin/curl -s localhost\n'


def write_app(root, go_mod=GO_MOD, mains=('main.go',)):
    app = root / 'webapp/go'
    app.mkdir(parents=True)
    (app / 'go.mod').write_text(go_mod)
    for name in mains:
        (app / name).parent.mkdir(parents=True, exist_ok=True)
        (app / name).write_text('package main\n\nfunc main() {}\n')
    (app / mains[0]).with_name('handlers.go').write_text('package main\n')
    return app


class BuildLaneTests(unittest.TestCase):
    def test_configuration_arrival_starts_build_without_second_command(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            config = root / 'config.json'
            settings = dict(target='linux/amd64', binary='fixture')
            def supply(_):
                self.assertEqual(json.loads((root/'state/status.json').read_text())['state'], 'needs_configuration')
                config.write_text(json.dumps(settings))
            calls = []
            def run(args, **kwargs):
                calls.append(args)
                return subprocess.CompletedProcess(args, 0, 'x86_64\nfixture OS', '')
            with patch.multiple(build, ROOT=root, STATE=root/'state', CONFIG=config), patch.object(build.subprocess, 'run', run), patch.object(build.time, 'sleep', supply):
                build.run('app1', 'webapp/go')
            self.assertEqual(json.loads((root/'state/status.json').read_text())['state'], 'complete')
            self.assertEqual([c[2] for c in calls[1:]], ['validate', 'build'])
            manifest = json.loads((root/'state/manifest.json').read_text())
            self.assertEqual(manifest['local_builds'][0], dict(settings, item='go-app'))
            # The time spent waiting for settings is recorded beside kickoff's stages.
            rows = [line.split('\t') for line in (root/'.local/stage-timing.tsv').read_text().splitlines()]
            self.assertEqual([row[2] for row in rows], ['inspecting', 'needs_configuration', 'building', 'complete'])
            self.assertTrue(all(row[1] == 'kickoff-build' and row[4] == '0' for row in rows))

    def test_probe_failure_never_compiles(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.multiple(build, ROOT=root, STATE=root/'state', CONFIG=root/'config.json'), patch.object(build.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'SSH failed')) as run:
                with self.assertRaises(RuntimeError):
                    build.run('app1', 'webapp/go')
                self.assertEqual(run.call_count, 1)
                self.assertEqual(json.loads((root/'state/status.json').read_text())['state'], 'failed')

    def test_the_probe_lists_unit_commands(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            calls = []
            def run(args, **kwargs):
                calls.append(args)
                return subprocess.CompletedProcess(args, 1, '', 'stop here')
            with patch.multiple(build, ROOT=root, STATE=root/'state', CONFIG=root/'config.json'), patch.object(build.subprocess, 'run', run):
                with self.assertRaises(RuntimeError):
                    build.run('app1', 'webapp/go')
            # The probe runs on the node; here it runs on fixture unit files.
            units = root / 'units'
            units.mkdir()
            (units / 'isuride-go.service').write_text('[Service]\nExecStart=/home/isucon/webapp/go/isuride\n')
            probe = calls[0][2].replace('/etc/systemd/system', str(units))
            out = subprocess.run(['sh', '-c', probe], capture_output=True, text=True).stdout
            self.assertIn('unit\tisuride-go.service\t/home/isucon/webapp/go/isuride\n', out)


class ProposalTests(unittest.TestCase):
    """Settings the build lane writes by itself, and the cases it leaves to the operator."""

    def proposal(self, environment=PROBE, **app):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_app(root, **app)
            with patch.multiple(build, ROOT=root):
                return build.propose(environment, 'webapp/go')

    def test_the_go_unit_names_the_binary_and_where_it_goes(self):
        settings, reasons = self.proposal()
        self.assertEqual(settings, {'target': 'linux/amd64', 'binary': 'isuride', 'output': 'isuride', 'package': '.'})
        self.assertIn('/home/isucon/webapp/go/isuride', reasons[0])
        settings, _ = self.proposal(PROBE.replace('x86_64', 'aarch64').replace('webapp/go/isuride', 'webapp/go/bin/app'),
                                    mains=('cmd/app/main.go',))
        self.assertEqual(settings, {'target': 'linux/arm64', 'binary': 'app', 'output': 'bin/app', 'package': './cmd/app'})

    def test_unclear_cases_wait_for_the_operator(self):
        for environment, app in [
                (PROBE.replace('isuride-go.service', 'isuride.service'), {}),
                (PROBE + 'unit\tisuride.golang.service\t/home/isucon/webapp/go/other\n', {}),
                (PROBE.replace('/home/isucon/webapp/go/isuride', '/usr/local/bin/isuride'), {}),
                (PROBE, dict(mains=('main.go', 'tools/gen/main.go'))),
                (PROBE, dict(go_mod=GO_MOD.replace(')', '\tgithub.com/mattn/go-sqlite3 v1.14.22\n)')))]:
            with self.subTest(environment=environment, **app):
                settings, reasons = self.proposal(environment, **app)
                self.assertIsNone(settings)
                self.assertTrue(reasons)
        self.assertIsNone(self.proposal(PROBE.replace('x86_64', 'riscv64'))[0])
        with tempfile.TemporaryDirectory() as d:
            with patch.multiple(build, ROOT=Path(d)):
                settings, reasons = build.propose(PROBE, 'webapp/go')
        self.assertIsNone(settings)
        self.assertIn('go.mod not found', reasons[0])

    def test_proposal_is_written_and_built_without_waiting(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_app(root)
            calls = []
            def run(args, **kwargs):
                calls.append(args)
                return subprocess.CompletedProcess(args, 0, PROBE, '')
            with patch.multiple(build, ROOT=root, STATE=root/'state', CONFIG=root/'config/kickoff-build.json'), \
                    patch.object(build.subprocess, 'run', run), patch.object(build.time, 'sleep', side_effect=AssertionError('waited')):
                (root / 'config').mkdir()
                build.run('app1', 'webapp/go')
            self.assertEqual(json.loads((root/'config/kickoff-build.json').read_text())['binary'], 'isuride')
            states = [line.split('\t')[2] for line in (root/'.local/stage-timing.tsv').read_text().splitlines()]
            self.assertEqual(states, ['inspecting', 'configured', 'building', 'complete'])
            # The agent following progress.log learns the settings were written for it to review.
            log = (root/'.local/progress.log').read_text()
            self.assertIn('kickoff-build\tconfigured', log)
            self.assertIn('kickoff-build\tcomplete', log)

    def test_auto_configuration_can_be_turned_off(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_app(root)
            def run(args, **kwargs):
                return subprocess.CompletedProcess(args, 0, PROBE, '')
            with patch.multiple(build, ROOT=root, STATE=root/'state', CONFIG=root/'config.json'), \
                    patch.dict(os.environ, {'KICKOFF_BUILD_AUTO_CONFIG': '0'}), \
                    patch.object(build.subprocess, 'run', run), patch.object(build.time, 'sleep', side_effect=RuntimeError('stop')), \
                    patch.object(build.time, 'monotonic', return_value=0):
                with self.assertRaises(RuntimeError):
                    build.run('app1', 'webapp/go')
            self.assertFalse((root/'config.json').exists())
            self.assertIn('settings', json.loads((root/'state/proposal.json').read_text()))


class KickoffTests(unittest.TestCase):
    def exercise(self, history=True):
        with tempfile.TemporaryDirectory(prefix='initial space ') as d:
            root = Path(d)
            for folder in ('scripts', 'config', 'webapp/go', 'webapp/sql', '.local'):
                (root/folder).mkdir(parents=True)
            for name in ('kickoff.sh', 'application-policy.sh', 'stage-lib.sh', 'progress.py', 'check-agent-session.py',
                         'benchmark-markers.py'):
                shutil.copy(ROOT/'scripts'/name, root/'scripts'/name)
            (root/'config/application.env').write_text('APPLICATION_LANGUAGE=go\nAPPLICATION_PATH=webapp/go\n')
            (root/'webapp/go/go.mod').write_text('module fixture\n')
            (root/'webapp/go/main.go').write_text('mux.HandleFunc("POST /api/initialize", postInitialize)\n')
            if history:
                (root/'docs/agent-history').mkdir(parents=True)
                (root/'docs/agent-history/20261013-000000.md').write_text(
                    '# Agent conversation\n\n- Agent: `claude`\n- Session: `fixture-session`\n')
            (root/'.local/ansible-inventory.json').write_text(json.dumps({'all': {'children': {'application': {'hosts': {'app1': {}}}}}}))
            for args in (['init', '-q'], ['add', '.'], ['-c','user.name=test','-c','user.email=test@example.com','commit','-qm','fixture']):
                subprocess.run(['git','-C',d,*args],check=True)
            for name, event in [('bootstrap.sh','bootstrap'), ('inspect-environment.sh','inspect'), ('configure-draft.sh','draft')]:
                (root/'scripts'/name).write_text('#!/bin/sh\necho '+event+' >> events\n')
            for p in (root/'scripts').glob('*.sh'):p.chmod(0o755)
            (root/'scripts/kickoff-build.py').write_text('import sys\nopen("events","a").write("build-"+sys.argv[1]+"\\n")\n')
            (root/'scripts/lane.py').write_text('import sys\nopen("events","a").write("lanes-"+sys.argv[1]+"\\n")\n')
            (root/'scripts/review-draft.py').write_text('')
            environment = {k: v for k, v in os.environ.items() if not k.startswith(('CODEX_', 'CLAUDE_CODE_SESSION'))}
            environment.update(ISUSCOPE_LOCK_HELD='1', CLAUDE_CODE_SESSION_ID='fixture-session')
            # kickoff commits the initialize route; a CI runner has no git identity of its own.
            environment.update(GIT_AUTHOR_NAME='test', GIT_AUTHOR_EMAIL='test@example.com',
                               GIT_COMMITTER_NAME='test', GIT_COMMITTER_EMAIL='test@example.com')
            result=subprocess.run(['bash','scripts/kickoff.sh'],cwd=root,env=environment,capture_output=True,text=True)
            if not history:
                # A session started elsewhere stops before anything reaches a node.
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root/'events').exists())
                self.assertIn('Start a new Claude Code session inside', result.stderr)
                return
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root/'events').read_text().splitlines(),
                             ['build-start','lanes-kickoff','bootstrap','inspect','draft','build-status'])
            # Nested steps keep their own timing although the outer lock records only kickoff.
            stages = [line.split('\t')[1:3] for line in (root/'.local/stage-timing.tsv').read_text().splitlines()]
            self.assertEqual(stages, [['kickoff.sh', name] for name in
                                      ('agent-session', 'build-dispatch', 'lanes', 'bootstrap', 'inspect',
                                       'configure-draft', 'review-draft')])
            # The agent following progress.log sees when code can be read and when the draft is ready.
            states = [line.split('\t')[1:3] for line in (root/'.local/progress.log').read_text().splitlines()]
            self.assertEqual(states, [['kickoff', 'started'], ['kickoff', 'code-ready'], ['kickoff', 'initialize-route'],
                                      ['kickoff', 'draft-ready']])
            # The one initialize route in the code is set and committed before the lanes start.
            self.assertIn('path = "/api/initialize"', (root/'.isuscope/routes.toml').read_text())
            self.assertEqual(subprocess.run(['git', '-C', d, 'status', '--porcelain', '--', '.isuscope'],
                                            capture_output=True, text=True).stdout, '')

    def test_build_starts_before_bootstrap_without_another_session(self):
        self.exercise()

    def test_kickoff_stops_first_when_the_session_records_its_history_elsewhere(self):
        self.exercise(history=False)


if __name__ == '__main__':
    unittest.main()
