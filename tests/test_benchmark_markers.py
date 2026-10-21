import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = """\
10:49:01.100 webappの初期化を開始します
10:49:01.200 [ADMIN] 初期化 internal detail
10:49:09.800 初期化が完了しました
10:49:09.900 負荷走行を開始します
10:50:22.022 score: 27845 (= 27845 - 0) : passed
"""


class BenchmarkMarkersTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'config').mkdir()
        shutil.copy(ROOT / 'scripts/benchmark-markers.py', self.root / 'scripts/benchmark-markers.py')
        (self.root / '.isuscope').mkdir()
        shutil.copy(ROOT / '.isuscope/policy.toml', self.root / '.isuscope/policy.toml')
        (self.root / 'config/benchmark-sample.log').write_text(SAMPLE)

    def tearDown(self):
        self.tmp.cleanup()

    def markers(self, *args):
        return subprocess.run(['python3', 'scripts/benchmark-markers.py', *args], cwd=self.root,
                              capture_output=True, text=True, env={**os.environ, 'REPO_DIR': str(self.root)})

    def test_suggest_offers_participant_lines_without_timestamps(self):
        output = self.markers('suggest').stdout
        self.assertIn('webappの初期化を開始します', output)
        self.assertIn('負荷走行を開始します', output)
        self.assertNotIn('ADMIN', output)
        self.assertNotIn('10:49', output)

    def test_set_checks_order_and_writes_the_policy(self):
        self.assertNotEqual(self.markers('check').returncode, 0)
        self.assertNotEqual(self.markers('set', '負荷走行を開始します', '初期化を開始').returncode, 0)
        self.assertNotEqual(self.markers('set', 'internal detail', '負荷走行').returncode, 0)  # operator line
        result = self.markers('set', 'webappの初期化を開始します', '負荷走行を開始します')
        self.assertEqual(result.returncode, 0, result.stderr)
        policy = tomllib.loads((self.root / '.isuscope/policy.toml').read_text())['benchmark']
        self.assertEqual(policy['initialize_start_marker'], 'webappの初期化を開始します')
        self.assertEqual(policy['operator_line_pattern'], '\\[ADMIN\\]')
        self.assertEqual(policy['prepare_command'], ['scripts/benchmark-prepare.sh'])  # the rest stays
        again = self.markers('set', 'webappの初期化を開始します', '負荷走行を開始します')
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(tomllib.loads((self.root / '.isuscope/policy.toml').read_text())['benchmark'], policy)
        self.assertEqual(self.markers('check').returncode, 0)

    def test_initialize_route_from_the_code_is_enough(self):
        source = self.root / 'webapp/rust/src/main.rs'
        source.parent.mkdir(parents=True)
        source.write_text('let app = Router::new()\n    .route("/api/initialize", post(initialize));\n')
        (self.root / '.isuscope/routes.toml').write_text(
            '# 大会固有routeはここへ追加します。\n\n[[routes]]\npattern = "^/a/[0-9]+$"\nreplace = "/a/:id"\n')
        self.assertIn('POST /api/initialize    (webapp/rust/src/main.rs:2)', self.markers('suggest-route').stdout)
        self.assertNotEqual(self.markers('set-route', 'post', '/api/initialize').returncode, 0)
        self.assertEqual(self.markers('set-route', 'POST', '/initialize').returncode, 0)
        self.assertEqual(self.markers('set-route', 'POST', '/api/initialize').returncode, 0)  # replaces, not adds
        import tomllib
        routes = tomllib.loads((self.root / '.isuscope/routes.toml').read_text())
        self.assertEqual(routes['initialize'], {'method': 'POST', 'path': '/api/initialize'})
        self.assertEqual(len(routes['routes']), 1)
        # The route alone satisfies the check, without markers or a sample.
        (self.root / 'config/benchmark-sample.log').unlink()
        self.assertEqual(self.markers('check').returncode, 0)


    def test_auto_route_sets_a_single_candidate_and_leaves_several_to_main(self):
        source = self.root / 'webapp/rust/src/main.rs'
        source.parent.mkdir(parents=True)
        source.write_text('    .route("/api/initialize", post(initialize))\n')
        result = self.markers('auto-route')
        self.assertEqual(result.returncode, 0, result.stderr)
        import tomllib
        routes = tomllib.loads((self.root / '.isuscope/routes.toml').read_text())
        self.assertEqual(routes['initialize'], {'method': 'POST', 'path': '/api/initialize'})
        self.assertIn('already POST /api/initialize', self.markers('auto-route').stdout)
        (self.root / '.isuscope/routes.toml').unlink()
        source.write_text('    .route("/api/initialize", post(a))\n    .route("/initialize", post(b))\n')
        result = self.markers('auto-route')
        self.assertEqual(result.returncode, 3)
        self.assertIn('2 initialize candidates', result.stderr)
        self.assertFalse((self.root / '.isuscope/routes.toml').exists())
        source.write_text('fn main() {}\n')
        self.assertEqual(self.markers('auto-route').returncode, 3)


if __name__ == '__main__':
    unittest.main()
