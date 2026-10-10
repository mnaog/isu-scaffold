"""Regression fixtures for score and PASS/FAIL extraction in the standard benchmark adapter."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Shape of an ISUCON benchmarker log: periodic scores, tags whose counters are numbers,
# a final result line, and per-scenario counts printed after it.
FINAL_OUTPUT = """\
10:49:04.792596 score: 1303 (= 1303 - 0) : passed
10:49:04.792691 tag: _C7.RegisterScore                : 0
10:49:49.792697 score: 22975 (= 22975 - 0) : passed
10:49:49.792788 tag: _C7.RegisterScore                : 230
10:50:22.022121 score: 27845 (= 27845 - 0) : passed
10:50:22.022135 deductionCount: 0, timeoutCount: 72
10:50:22.022142 SubmitAssignment    : 8565 回 (25695 点)
10:50:22.022204 tag: _C7.RegisterScore                : 439
"""
FAILED_OUTPUT = """\
10:49:04.792596 score: 1303 (= 1303 - 0) : passed
10:50:22.022121 score: 271041 (= 276091 - 5050) : failed
10:50:22.022204 tag: _C7.RegisterScore                : 12
"""
SETTINGS = {
    'BENCHMARK_TRANSPORT': 'local',
    'BENCHMARK_PROBE_COMMAND': 'true',
    'BENCHMARK_SAMPLE_FILE': 'config/benchmark-sample.log',
    'BENCHMARK_SAMPLE_SCORE': '27845',
    'BENCHMARK_SAMPLE_PASS': 'true',
    'BENCHMARK_SCORE_REGEX': '^[0-9:.]+[[:space:]]+score:[[:space:]]*[0-9]+',
    'BENCHMARK_PASS_REGEX': '^[0-9:.]+[[:space:]]+score:.*:[[:space:]]*passed$',
    'BENCHMARK_FAIL_REGEX': '^[0-9:.]+[[:space:]]+score:.*:[[:space:]]*failed$',
    'BENCHMARK_TIMEOUT_SECONDS': '0',
}


def shell_quote(value):
    return "'" + value.replace("'", "'\"'\"'") + "'"


class AdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ['scripts/benchmark-contract.py', 'scripts/timeout-command.py']:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, self.root / name)
        (self.root / 'config').mkdir(exist_ok=True)
        (self.root / 'config/isuscope-policy.json').write_text(json.dumps({'operator_line_pattern': '\\[ADMIN\\]'}))
        (self.root / 'config/benchmark-sample.log').write_text(FINAL_OUTPUT)
        self.output = self.root / 'output.log'

    def configure(self, output, exit_status=0, **overrides):
        self.output.write_text(output)
        settings = dict(SETTINGS, BENCHMARK_COMMAND=f'printf "%s\\n" "$(cat {self.output})"; exit {exit_status}')
        settings.update(overrides)
        (self.root / 'config/benchmark.env').write_text(
            ''.join(f'{key}={shell_quote(value)}\n' for key, value in settings.items()))
        subprocess.run(['python3', str(ROOT / 'tests/benchmark-contract-fixture.py')], cwd=self.root,
                       env=dict(os.environ, **settings), check=True)

    def adapter(self, *args):
        run_dir = self.root / 'run'
        run_dir.mkdir(exist_ok=True)
        return subprocess.run(['bash', str(ROOT / '.isuscope/benchmark.sh'), *args], capture_output=True, text=True,
                              env=dict(os.environ, ISUSCOPE_PROJECT_ROOT=str(self.root), ISUSCOPE_RUN_DIR=str(run_dir),
                                       ISUSCOPE_LOCK_HELD='1'))

    def result(self):
        completed = self.adapter()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_final_score_is_not_taken_from_trailing_tag_numbers(self):
        self.configure(FINAL_OUTPUT)
        result = self.result()
        self.assertEqual((result['score'], result['pass']), (27845, True))
        self.assertIn('score line: 10:50:22.022121 score: 27845 (= 27845 - 0) : passed', result['messages'])

    def test_loose_regex_that_reads_tag_numbers_fails_check(self):
        loose = '([Ss]core[^0-9]*[0-9]+)'
        self.configure(FINAL_OUTPUT, BENCHMARK_SCORE_REGEX=loose)
        completed = self.adapter('--check')
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('extracted 439 from the sample, expected 27845', completed.stderr)

    def test_default_regex_ignores_tag_numbers(self):
        default = next(line.split('=', 1)[1].strip("'") for line in (ROOT / 'config/benchmark.env').read_text().splitlines()
                       if line.startswith('BENCHMARK_SCORE_REGEX='))
        self.configure(FINAL_OUTPUT, BENCHMARK_SCORE_REGEX=default)
        self.assertEqual(self.adapter('--check').returncode, 0, self.adapter('--check').stderr)
        self.assertEqual(self.result()['score'], 27845)

    def test_failed_final_line_wins_over_earlier_passed_lines(self):
        self.configure(FAILED_OUTPUT)
        result = self.result()
        self.assertEqual((result['score'], result['pass']), (271041, False))

    def test_nonzero_exit_status_fails_even_with_passed_line(self):
        self.configure(FINAL_OUTPUT, exit_status=7)
        result = self.result()
        self.assertEqual((result['score'], result['pass']), (27845, False))
        self.assertIn('benchmark exit status 7', result['messages'])

    def test_truncated_output_without_final_line_is_not_passed(self):
        truncated = '10:49:04.792596 score: 1303 (= 1303 - 0) : progress\n'
        self.configure(truncated)
        result = self.result()
        self.assertFalse(result['pass'])
        self.assertIn('no final PASS/FAIL line matched', result['messages'])

    def test_output_without_any_score_is_an_adapter_failure(self):
        self.configure('connection reset by peer\n', exit_status=1)
        completed = self.adapter()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('did not match BENCHMARK_SCORE_REGEX (exit status 1)', completed.stderr)

    def test_operator_lines_are_excluded(self):
        self.configure(FINAL_OUTPUT + '10:50:23.000000 [ADMIN] score: 99999 (= 99999 - 0) : passed\n'
                       '[ADMIN] 10:50:23.000000 score: 88888 (= 88888 - 0) : passed\n')
        result = self.result()
        self.assertEqual(result['score'], 27845)
        self.assertFalse(any('ADMIN' in message for message in result['messages']))

    def test_sample_expectations_are_required_and_checked(self):
        for overrides, error in [({'BENCHMARK_SAMPLE_SCORE': ''}, 'BENCHMARK_SAMPLE_SCORE'),
                                 ({'BENCHMARK_SAMPLE_PASS': 'yes'}, 'BENCHMARK_SAMPLE_PASS'),
                                 ({'BENCHMARK_SAMPLE_PASS': 'false'}, 'judged the sample as pass=true')]:
            with self.subTest(overrides=overrides):
                self.configure(FINAL_OUTPUT, **overrides)
                completed = self.adapter('--check')
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(error, completed.stderr)


if __name__ == '__main__':
    unittest.main()
