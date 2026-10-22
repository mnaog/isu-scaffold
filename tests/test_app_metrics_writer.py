"""lane state-logがアプリへ写すアプリ計測の書き手（config/app-metrics/app_metrics.rs）を、単独のcrateで確かめる。

isuscopeとの約束（collectorの標準のmetric行）を守ること、lockの待ちが数えられること、
環境変数で止められることを見る。cargoが無い端末では飛ばす。
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
WRITER = ROOT / 'config/app-metrics/app_metrics.rs'

MAIN = r'''
mod app_metrics;

use std::sync::Mutex;
use std::time::Duration;

static STORE: Mutex<u64> = Mutex::new(0);
static STORE_LOCK: app_metrics::Lock = app_metrics::Lock::new("store");

fn main() {
    let threads = (0..4)
        .map(|thread| {
            std::thread::spawn(move || {
                for index in 0..50 {
                    // 4本が同じlockを1msずつ持つので、待ちが出る。
                    let mut store = STORE_LOCK.acquire(|| STORE.lock().unwrap());
                    *store += 1;
                    std::thread::sleep(Duration::from_millis(1));
                    drop(store);
                    let outcome = if index % 3 == 0 { "502".to_string() } else { "204".to_string() };
                    app_metrics::call("payment", outcome, Duration::from_millis(thread + 1));
                }
            })
        })
        .collect::<Vec<_>>();
    for thread in threads {
        thread.join().unwrap();
    }
    assert_eq!(*STORE.lock().unwrap(), 200);
    // 書き手は1秒ごとに書くので、最後の分が届くまで待つ。
    std::thread::sleep(Duration::from_millis(1300));
}
'''


@unittest.skipUnless(shutil.which('cargo'), 'cargo is not installed')
class AppMetricsWriterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.crate = Path(cls.tmp.name) / 'crate'
        (cls.crate / 'src').mkdir(parents=True)
        (cls.crate / 'Cargo.toml').write_text(
            '[package]\nname = "app_metrics_check"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n'
        )
        shutil.copyfile(WRITER, cls.crate / 'src/app_metrics.rs')
        (cls.crate / 'src/main.rs').write_text(MAIN)
        cls.env = {**os.environ, 'CARGO_TARGET_DIR': str(Path(cls.tmp.name) / 'target')}
        subprocess.run(['cargo', 'build', '--quiet', '--offline'], cwd=cls.crate, env=cls.env, check=True)
        cls.binary = cls.copy(Path(cls.tmp.name) / 'target/debug/app_metrics_check')

    @classmethod
    def copy(cls, executable):
        # macOSでは、cargoが書いた場所のままの実行fileがcode署名の検査でSIGKILLされることがある。
        copied = Path(cls.tmp.name) / f'run-{Path(executable).name}'
        shutil.copyfile(executable, copied)
        copied.chmod(0o755)
        return copied

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_writer(self, path):
        env = {**self.env, 'ISUSCOPE_APP_METRICS': '' if path is None else str(path)}
        subprocess.run([str(self.binary)], env=env, check=True, timeout=60)

    def test_unit_tests_of_the_writer_pass(self):
        built = subprocess.run(['cargo', 'test', '--no-run', '--offline', '--message-format=json'],
                               cwd=self.crate, env=self.env, capture_output=True, text=True, check=True)
        executables = [record['executable'] for record in map(json.loads, built.stdout.splitlines())
                       if record.get('reason') == 'compiler-artifact' and record.get('executable')]
        self.assertEqual(len(executables), 1, built.stdout)
        result = subprocess.run([str(self.copy(executables[0]))], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('5 passed', result.stdout)

    def test_lines_are_isuscope_metric_records_with_calls_and_lock_waits(self):
        path = Path(self.tmp.name) / 'app.jsonl'
        self.run_writer(path)
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertTrue(lines)
        for line in lines:
            self.assertEqual(line['type'], 'metric')
            self.assertTrue(line['name'].startswith('app.'), line)
            self.assertIsInstance(line['timestamp'], (int, float))
            self.assertGreater(line['timestamp'], 1_600_000_000, 'Unix seconds')
            self.assertTrue(all(isinstance(value, str) for value in line['labels'].values()))

        def total(name, **labels):
            return sum(line['value'] for line in lines
                       if line['name'] == name and all(line['labels'].get(k) == v for k, v in labels.items()))

        self.assertEqual(total('app.call.count', target='payment', outcome='502'), 4 * 17)
        self.assertEqual(total('app.call.count', target='payment', outcome='204'), 4 * 33)
        self.assertEqual(total('app.call.duration_sum', target='payment'), 50 * (1 + 2 + 3 + 4))
        self.assertEqual(max(line['value'] for line in lines if line['name'] == 'app.call.duration_max'), 4)
        self.assertEqual(total('app.lock.count', lock='store'), 200)
        self.assertGreaterEqual(total('app.lock.hold_sum', lock='store'), 200)
        self.assertGreater(total('app.lock.wait_sum', lock='store'), 0, 'four threads contend for one lock')

    def test_an_empty_variable_writes_nothing(self):
        default = Path('/var/tmp/isuscope-app-metrics.jsonl')
        before = default.stat().st_mtime_ns if default.exists() else None
        self.run_writer(None)
        after = default.stat().st_mtime_ns if default.exists() else None
        self.assertEqual(before, after)


class AppMetricsLaneTest(unittest.TestCase):
    def test_the_state_log_lane_copies_the_writer_and_the_export_carries_it(self):
        lanes = json.loads((ROOT / 'config/lanes.json').read_text())
        prompt = (ROOT / lanes['lanes']['state-log']['prompt']).read_text()
        self.assertIn('config/app-metrics/app_metrics.rs', prompt)
        export = json.loads((ROOT / 'config/scaffold-export.json').read_text())['files']
        self.assertIn('config/app-metrics/app_metrics.rs', export)


if __name__ == '__main__':
    unittest.main()
