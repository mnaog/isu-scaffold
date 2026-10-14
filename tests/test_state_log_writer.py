"""lane state-logがアプリへ写す書き手（config/state-log/state_log.rs）を、単独のcrateで確かめる。

isuscopeとの約束（1行の形）を守ること、送り手を待たせないこと、環境変数で止められることを見る。
cargoが無い端末では飛ばす。
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
WRITER = ROOT / 'config/state-log/state_log.rs'

MAIN = r'''
mod state_log;

fn main() {
    let threads = (0..4)
        .map(|thread| {
            std::thread::spawn(move || {
                for index in 0..250 {
                    let id = format!("o{thread}-{index}");
                    state_log::created("order", &id, "PENDING", &[("user", format!("u{index}").into())]);
                    state_log::changed("order", &id, "PENDING", "PAID", &[("amount", (index * 10).into())]);
                    let mut log = state_log::Pending::new();
                    log.entered("order", &id, "SHIPPED", &[("note", "say \"hi\"\n".into())]);
                    log.emit();
                    // commitしなかったtransactionの変化は書かない。
                    let mut dropped = state_log::Pending::new();
                    dropped.changed("order", &id, "SHIPPED", "CANCELLED", &[]);
                    drop(dropped);
                }
            })
        })
        .collect::<Vec<_>>();
    for thread in threads {
        thread.join().unwrap();
    }
    std::thread::sleep(std::time::Duration::from_millis(400));
}
'''


@unittest.skipUnless(shutil.which('cargo'), 'cargo is not installed')
class StateLogWriterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.crate = Path(cls.tmp.name) / 'crate'
        (cls.crate / 'src').mkdir(parents=True)
        (cls.crate / 'Cargo.toml').write_text(
            '[package]\nname = "state_log_check"\nversion = "0.1.0"\nedition = "2021"\n\n[workspace]\n'
        )
        shutil.copyfile(WRITER, cls.crate / 'src/state_log.rs')
        (cls.crate / 'src/main.rs').write_text(MAIN)
        cls.env = {**os.environ, 'CARGO_TARGET_DIR': str(Path(cls.tmp.name) / 'target')}
        subprocess.run(['cargo', 'build', '--quiet', '--offline'], cwd=cls.crate, env=cls.env, check=True)
        cls.binary = cls.copy(Path(cls.tmp.name) / 'target/debug/state_log_check')

    @classmethod
    def copy(cls, executable):
        # macOSでは、cargoが書いた場所のままの実行fileがcode署名の検査でSIGKILLされることがある
        # （内容が前に作ったものと同じとき）。別の場所へ写してから動かす。
        copied = Path(cls.tmp.name) / f'run-{Path(executable).name}'
        shutil.copyfile(executable, copied)
        copied.chmod(0o755)
        return copied

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_writer(self, path):
        env = {**self.env, 'ISUSCOPE_STATE_LOG': '' if path is None else str(path)}
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

    def test_lines_follow_the_isuscope_format(self):
        path = Path(self.tmp.name) / 'state.jsonl'
        self.run_writer(path)
        lines = [json.loads(line) for line in path.read_text().splitlines()]
        events = [line for line in lines if 'kind' in line]
        self.assertEqual(sum(line.get('dropped', 0) for line in lines), 0)
        self.assertEqual(len(events), 4 * 250 * 3)
        for event in events:
            self.assertIsInstance(event['t'], int)
            self.assertGreater(event['t'], 1_600_000_000_000_000, 'microseconds since the epoch')
            self.assertIsInstance(event['seq'], int)
            self.assertEqual(event['kind'], 'order')
            self.assertIsInstance(event['id'], str)
            self.assertIn(event['to'], ('PENDING', 'PAID', 'SHIPPED'))
        by_state = {event['to']: event for event in events if event['id'] == 'o1-7'}
        self.assertIsNone(by_state['PENDING']['from'], 'created writes "from": null')
        self.assertEqual(by_state['PAID']['from'], 'PENDING')
        self.assertNotIn('from', by_state['SHIPPED'], 'entered leaves "from" out')
        self.assertEqual(by_state['PAID']['attrs'], {'amount': 70})
        self.assertEqual(by_state['SHIPPED']['attrs'], {'note': 'say "hi"\n'})
        self.assertNotIn('CANCELLED', {event['to'] for event in events})
        self.assertEqual(len({event['seq'] for event in events}), len(events))

    def test_an_empty_variable_writes_nothing(self):
        # 空なら既定のpathへも書かない（最終計測の前に止めるための約束）。
        default = Path('/var/tmp/isuscope-state.jsonl')
        before = default.stat().st_mtime_ns if default.exists() else None
        self.run_writer(None)
        after = default.stat().st_mtime_ns if default.exists() else None
        self.assertEqual(before, after)


class StateLogLaneTest(unittest.TestCase):
    def test_kickoff_starts_the_state_log_lane_with_a_prompt_that_copies_the_writer(self):
        lanes = json.loads((ROOT / 'config/lanes.json').read_text())
        self.assertIn('state-log', lanes['kickoff'])
        prompt = (ROOT / lanes['lanes']['state-log']['prompt']).read_text()
        self.assertIn('config/state-log/state_log.rs', prompt)
        export = json.loads((ROOT / 'config/scaffold-export.json').read_text())['files']
        for path in ('config/lanes/state-log.md', 'config/state-log/state_log.rs'):
            self.assertIn(path, export)


if __name__ == '__main__':
    unittest.main()
