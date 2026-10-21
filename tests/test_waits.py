"""APIごとの待ち: mainが値を変えるscripts/waits.py、runに残すidentity、アプリへ写すconfig/waits/waits.rs。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent

UNIT = '[Service]\nEnvironmentFile=/home/isucon/env.sh\nExecStart=/home/isucon/webapp/rust/target/release/app\n'
SYNC = {'items': [
    {'name': 'rust-service', 'local': 'config/systemd/app.service', 'remote': '/etc/systemd/system/app.service', 'type': 'file'},
    {'name': 'app-env', 'local': 'config/app/env.sh', 'remote': '/home/isucon/env.sh', 'type': 'file'},
]}


class WaitsScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / 'scripts').mkdir()
        for name in ('waits.py', 'benchmark-identity.py'):
            shutil.copy(ROOT / 'scripts' / name, self.repo / 'scripts' / name)
        (self.repo / 'config/systemd').mkdir(parents=True)
        (self.repo / 'config/app').mkdir(parents=True)
        (self.repo / 'config/systemd/app.service').write_text(UNIT)
        (self.repo / 'config/app/env.sh').write_text('ISUCON_DB_HOST=127.0.0.1\n')
        (self.repo / 'config/sync.json').write_text(json.dumps(SYNC))
        self.env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.com',
                        GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.com')
        for args in (['init', '-q', '-b', 'main'], ['add', '.'], ['commit', '-qm', 'fixture']):
            subprocess.run(['git', *args], cwd=self.repo, check=True, env=self.env)

    def tearDown(self):
        self.tmp.cleanup()

    def waits(self, *args, ok=True):
        result = subprocess.run(['python3', 'scripts/waits.py', *args], cwd=self.repo, capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def identity(self):
        out = subprocess.run(['python3', 'scripts/benchmark-identity.py'], cwd=self.repo, capture_output=True, text=True,
                             check=True).stdout
        return json.loads(out)

    def commit(self, message):
        subprocess.run(['git', 'commit', '-qam', message], cwd=self.repo, check=True, env=self.env)

    def test_set_finds_the_unit_s_environment_file_and_edits_one_line(self):
        self.assertNotIn('waits', self.identity())  # no table, no field
        out = self.waits('set', 'GET', '/api/app/nearby-chairs', '--after', '30', '--max-body', '40').stdout
        self.assertIn('--change wait-get-api-app-nearby-chairs-after-30', out)
        env = (self.repo / 'config/app/env.sh').read_text()
        self.assertIn('ISUCON_DB_HOST=127.0.0.1\n', env)
        self.assertIn('ISUCON_WAITS="GET /api/app/nearby-chairs after 30 max_body=40"', env)
        # The same route and phase is replaced, a placeholder spelled differently is the same route.
        self.waits('set', 'POST', '/api/app/rides/:ride_id/evaluation', '--before', '5')
        self.waits('set', 'post', '/api/app/rides/{id}/evaluation', '--before', '10')
        self.waits('set', 'GET', '/api/app/nearby-chairs', '--after', '100')
        self.assertEqual((self.repo / 'config/app/env.sh').read_text().count('ISUCON_WAITS='), 1)
        line = [l for l in (self.repo / 'config/app/env.sh').read_text().splitlines() if l.startswith('ISUCON_WAITS=')]
        self.assertEqual(line, ['ISUCON_WAITS="POST /api/app/rides/{id}/evaluation before 10;GET /api/app/nearby-chairs after 100"'])
        self.commit('Wait')
        self.assertEqual(self.identity()['waits'],
                         'POST /api/app/rides/{id}/evaluation before 10;GET /api/app/nearby-chairs after 100')
        # 0 drops one phase, --off drops the route.
        self.waits('set', 'POST', '/api/app/rides/:ride_id/evaluation', '--before', '0')
        self.waits('set', 'GET', '/api/app/nearby-chairs', '--off')
        self.assertIn('ISUCON_WAITS=""', (self.repo / 'config/app/env.sh').read_text())
        self.commit('No waits')
        self.assertNotIn('waits', self.identity())

    def test_a_wrong_rule_is_refused_before_it_reaches_the_app(self):
        refused = self.waits('set', 'GET', '/x', '--after', '5000', ok=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('0〜2000', refused.stderr)
        refused = self.waits('set', 'GET', '/x', '--before', '10', '--max-body', '3', ok=False)
        self.assertIn('afterだけ', refused.stderr)
        self.assertNotIn('ISUCON_WAITS', (self.repo / 'config/app/env.sh').read_text())

    def test_history_reads_the_runs_and_the_wait_changes(self):
        runs = self.repo / 'isuscope-data/runs'
        for index, (score, waits, tags) in enumerate([(700000, None, []), (760000, 'GET /a after 30', ['wait', 'wait:GET /a']),
                                                      (755000, 'GET /a after 30', [])]):
            run_id = f'01a15900-0000-7000-8000-00000000000{index}'
            (runs / run_id).mkdir(parents=True)
            values = {'deployed_files': f'f{index}'}
            if waits:
                values['waits'] = waits
            (runs / run_id / 'run.json').write_text(json.dumps({
                'id': run_id, 'state': 'complete', 'started_at': f'2026-10-20T12:0{index}:00Z', 'tags': tags,
                'benchmark': {'score': score, 'passed': True}, 'identity': {'values': values}}))
        change = self.repo / 'isuscope-data/changes/wait-get-a-after-30'
        (change / 'decisions').mkdir(parents=True)
        (change / 'change.json').write_text(json.dumps({'id': 'wait-get-a-after-30', 'created_at': '2026-10-20T12:02:00Z'}))
        (change / 'decisions/d.json').write_text(json.dumps({
            'created_at': '2026-10-20T12:02:00Z', 'status': 'accepted', 'reason': '振れ幅より上',
            'evidence': [{'run_id': '01a15900-0000-7000-8000-000000000001'}]}))
        out = self.waits('history').stdout
        self.assertIn('00000001 PASS    760000  waits="GET /a after 30"', out)
        self.assertNotIn('00000002', out)  # same table, no tag
        self.assertIn('wait-get-a-after-30: accepted（run 00000001）', out)
        self.assertIn('振れ幅より上', out)


MAIN = r'''
mod waits;

fn main() {
    let (table, errors) = waits::Waits::parse(
        "GET /api/app/nearby-chairs after 100 max_body=40; POST /api/app/rides/:ride_id/evaluation before 9999;\
         PUT /x sideways 1; GET /api/chair/notification after 20 status=200");
    println!("errors {}", errors.len());
    println!("describe {}", table.describe());
    let ms = |d: Option<std::time::Duration>| d.map(|d| d.as_millis() as i64).unwrap_or(-1);
    println!("empty-body {}", ms(table.after("GET", "/api/app/nearby-chairs", 200, Some(30))));
    println!("full-body {}", ms(table.after("GET", "/api/app/nearby-chairs", 200, Some(500))));
    println!("unknown-body {}", ms(table.after("GET", "/api/app/nearby-chairs", 200, None)));
    println!("placeholder {}", ms(table.before("post", "/api/app/rides/01ABC/evaluation")));
    println!("deeper {}", ms(table.before("POST", "/api/app/rides/01ABC/evaluation/x")));
    println!("status-miss {}", ms(table.after("GET", "/api/chair/notification", 204, None)));
    println!("status-hit {}", ms(table.after("GET", "/api/chair/notification", 200, None)));
    std::env::set_var(waits::VARIABLE, "");
    println!("global-empty {}", waits::global().is_empty());
}
'''


@unittest.skipUnless(shutil.which('cargo'), 'cargo is not installed')
class WaitsModuleTest(unittest.TestCase):
    def test_the_module_parses_matches_and_caps(self):
        with tempfile.TemporaryDirectory() as tmp:
            crate = Path(tmp) / 'crate'
            (crate / 'src').mkdir(parents=True)
            (crate / 'Cargo.toml').write_text('[package]\nname = "waits_check"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n')
            shutil.copyfile(ROOT / 'config/waits/waits.rs', crate / 'src/waits.rs')
            (crate / 'src/main.rs').write_text(MAIN)
            env = {**os.environ, 'CARGO_TARGET_DIR': str(Path(tmp) / 'target')}
            out = subprocess.run(['cargo', 'run', '--quiet', '--offline'], cwd=crate, env=env, capture_output=True,
                                 text=True, check=True).stdout
        lines = dict(line.split(' ', 1) for line in out.splitlines())
        self.assertEqual(lines['errors'], '1')
        self.assertEqual(lines['describe'], 'GET /api/app/nearby-chairs after 100 max_body=40;'
                         'POST /api/app/rides/:ride_id/evaluation before 2000;GET /api/chair/notification after 20 status=200')
        self.assertEqual(lines['empty-body'], '100')
        self.assertEqual(lines['full-body'], '-1')
        self.assertEqual(lines['unknown-body'], '-1')
        self.assertEqual(lines['placeholder'], '2000')
        self.assertEqual(lines['deeper'], '-1')
        self.assertEqual(lines['status-miss'], '-1')
        self.assertEqual(lines['status-hit'], '20')
        self.assertEqual(lines['global-empty'], 'true')


if __name__ == '__main__':
    unittest.main()
