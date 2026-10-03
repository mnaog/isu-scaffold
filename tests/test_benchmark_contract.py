import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('contract', ROOT / 'scripts/benchmark-contract.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        source = self.root / 'docs/official/manual.txt'
        source.parent.mkdir(parents=True)
        source.write_text('Synthetic official fixture: mode live, request limit 12s.')
        self.env = {'BENCHMARK_TRANSPORT': 'ssh', 'BENCHMARK_NODE': 'bench',
                    'BENCHMARK_COMMAND': "sudo -u runner bash -c '/bin/bench --mode=live --timeout=12s --target node1 | cat; exit 0'"}
        self.contract = {'schema_version': 1, 'reviewed_by': 'fixture', 'reviewed_at': 'fixture',
                         'conditions': dict.fromkeys(['mode', 'request_timeout', 'initialize_timeout', 'load_duration', 'target'], 'fixture'),
                         'sources': [{'path': 'docs/official/manual.txt', 'sha256': m.digest(source.read_bytes())}],
                         'invocation': {'executable': '/bin/bench', 'required_flags': {'--mode': 'live', '--timeout': '12s'}}}
        self.contract['effective_sha256'] = m.check(self.root, self.contract, self.env, seal=False)

    def test_verified_nested_command(self):
        m.check(self.root, self.contract, self.env)

    def test_bad_mode_missing_timeout_duplicate_rejected_even_when_sealing(self):
        original = self.env['BENCHMARK_COMMAND']
        for altered in [original.replace('live', 'test'), original.replace('--timeout=12s', ''),
                        original.replace('--mode=live', '--mode=live --mode=test'),
                        original.replace('--mode=live', '--mode=live -mode=test')]:
            with self.subTest(command=altered):
                self.env['BENCHMARK_COMMAND'] = altered
                with self.assertRaises(ValueError):
                    m.check(self.root, self.contract, self.env, seal=False)

    def test_target_and_command_override_invalidate_review(self):
        for key, value in [('BENCHMARK_NODE', 'other'), ('BENCHMARK_COMMAND', self.env['BENCHMARK_COMMAND'].replace('node1', 'node2'))]:
            with self.subTest(key=key):
                changed = dict(self.env, **{key: value})
                with self.assertRaisesRegex(ValueError, 'settings changed'):
                    m.check(self.root, self.contract, changed)

    def test_source_change_rejected(self):
        (self.root / 'docs/official/manual.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            m.check(self.root, self.contract, self.env)

    def test_http_body_change_rejected(self):
        body = self.root / 'body.json'
        body.write_text('{"mode":"live"}')
        self.env = {'BENCHMARK_TRANSPORT': 'http', 'BENCHMARK_HTTP_BODY_FILE': str(body)}
        self.contract['effective_sha256'] = m.check(self.root, self.contract, self.env, seal=False)
        body.write_text('{"mode":"test"}')
        with self.assertRaisesRegex(ValueError, 'settings changed'):
            m.check(self.root, self.contract, self.env)

    def test_unreviewed_missing_sources_rejected(self):
        for key, value in [('reviewed_by', ''), ('sources', [])]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                m.check(self.root, dict(self.contract, **{key: value}), self.env)

    def test_adapter_cannot_execute_without_review(self):
        (self.root / 'scripts').mkdir()
        (self.root / 'config').mkdir()
        shutil.copyfile(ROOT / 'scripts/benchmark-contract.py', self.root / 'scripts/benchmark-contract.py')
        marker = self.root / 'should-not-run'
        (self.root / 'config/benchmark.env').write_text(
            "BENCHMARK_TRANSPORT=local\nBENCHMARK_COMMAND='touch " + str(marker) + "'\n")
        for args in [[], ['--check'], ['--probe']]:
            result = subprocess.run(['bash', str(ROOT / '.isuscope/benchmark.sh'), *args],
                env=dict(os.environ, ISUSCOPE_PROJECT_ROOT=str(self.root)), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('benchmark contract rejected', result.stderr)
            self.assertFalse(marker.exists())
