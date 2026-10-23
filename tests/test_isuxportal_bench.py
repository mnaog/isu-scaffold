import http.server
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
import urllib.parse

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts/isuxportal-bench.py'
spec = importlib.util.spec_from_file_location('isuxportal_bench', SCRIPT)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
field = bench.field

TOKEN = 'tok+en/=='
INSTANCES = [(11, 1, '203.0.113.1', '10.0.0.1', 4), (12, 2, '203.0.113.2', '10.0.0.2', 4)]


def instance(id_, number, public, private, status):
    return field(7, id_) + field(3, number) + field(4, public) + field(5, private) + field(6, status)


def job(id_, status, target=11, result=None):
    data = field(1, id_) + field(2, 3) + field(3, target)
    if status:
        data += field(4, status)  # proto3 leaves PENDING (0) out
    if result is not None:
        data += field(18, result)
    return field(1, data)


def result(passed, score, stdout, reason):
    execution = field(1, reason) + field(2, stdout) + field(3, '') + field(4, -2)
    return field(1, True) + (field(2, True) if passed else b'') + field(3, score) + field(5, execution)


class FakePortal(http.server.BaseHTTPRequestHandler):
    """The contestant side of isuxportal as its page uses it: cookie session, CSRF on writes."""

    def log_message(self, *args):
        pass

    def send(self, status, body=b'', content_type=bench.PROTOBUF, headers=()):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def cookies(self):
        return dict(part.strip().split('=', 1) for part in self.headers.get('Cookie', '').split(';') if '=' in part)

    def error(self, status, name, message):
        self.send(status, field(1, status) + field(2, name) + field(3, message))

    def do_GET(self):
        state = self.server.state
        path = urllib.parse.urlsplit(self.path)
        if not self.cookies().get('_isuxportal_session'):
            return self.send(302, b'', 'text/html', [('Location', '/session/new/github')])
        if path.path in ('/contestant', '/'):
            page = (f'<html><head><meta name="csrf-param" content="authenticity_token" />'
                    f'<meta name="csrf-token" content="{TOKEN}" /></head></html>').encode()
            # Rails stores the CSRF secret in the session, so the page sets a new session cookie.
            return self.send(200, page, 'text/html', [('Set-Cookie', '_isuxportal_session=with-csrf; path=/; HttpOnly')])
        if path.path == '/api/session':
            body = field(1, field(1, 3) + field(2, 'team-a')) + field(2, field(1, 9) + field(3, 'me'))
            body += b''.join(field(5, instance(*item)) for item in INSTANCES)
            return self.send(200, body)
        if path.path == '/api/contestant/benchmark_jobs':
            if state.get('closed'):
                return self.error(404, 'NotFound', 'contest is not open')
            return self.send(200, b'')
        if path.path.startswith('/api/contestant/benchmark_jobs/'):
            state['polls'] = state.get('polls', 0) + 1
            replies = state['replies']
            return self.send(200, replies[min(state['polls'], len(replies)) - 1])
        self.send(404, b'', 'text/plain')

    def do_POST(self):
        state = self.server.state
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.headers.get('X-Csrf-Token') != TOKEN or self.cookies().get('_isuxportal_session') != 'with-csrf':
            return self.error(422, 'ActionController::InvalidAuthenticityToken', 'invalid csrf token')
        target = bench.Message(body).int(1)
        state['enqueued'] = target
        if target not in {item[0] for item in INSTANCES}:
            return self.error(404, 'NotFound', "Couldn't find ContestantInstance")
        self.send(200, job(7, 0, target))


class IsuxportalBenchTest(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakePortal)
        self.server.state = {}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.job_seconds = Path(scratch.name) / 'portal-job-seconds'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def bench(self, *args, cookie='_isuxportal_session=signed-in; other=1'):
        env = {k: v for k, v in os.environ.items() if not k.startswith('PORTAL_')}
        env['PORTAL_COOKIE'] = cookie
        env['PORTAL_JOB_SECONDS_FILE'] = str(self.job_seconds)
        return subprocess.run(['python3', str(SCRIPT), *args, '--portal', self.url, '--poll-seconds', '0.01'],
                               capture_output=True, text=True, env=env, timeout=30)

    def test_run_enqueues_waits_and_ends_with_the_line_the_adapter_reads(self):
        self.server.state['replies'] = [job(7, 1), job(7, 1), job(7, 4, result=result(True, 4321, 'score: 99 so far\n1 request failed\nall good\n', 'ok'))]
        done = self.bench('run', '--target-id', '12')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.server.state['enqueued'], 12)
        lines = done.stdout.splitlines()
        self.assertEqual(lines[0], 'ISUXPORTAL enqueued job=7 target=12')
        self.assertIn('ISUXPORTAL job=7 status=RUNNING', lines)
        self.assertEqual(lines.count('ISUXPORTAL job=7 status=RUNNING'), 1)
        self.assertIn('all good', lines)
        self.assertEqual(lines[-1], 'ISUXPORTAL job=7 status=FINISHED passed=true score=4321')
        self.assertNotIn('signed-in', done.stdout + done.stderr)
        # The documented regexes read only that line: not the bench's own 'score' or 'failed' lines.
        with tempfile.TemporaryDirectory() as d:
            output, config = Path(d) / 'out.txt', Path(d) / 'benchmark.env'
            output.write_text(done.stdout)
            config.write_text("BENCHMARK_SCORE_REGEX='^ISUXPORTAL .*score=[0-9]+'\n"
                              "BENCHMARK_PASS_REGEX='^ISUXPORTAL .*passed=true'\n"
                              "BENCHMARK_FAIL_REGEX='^ISUXPORTAL .*passed=false'\n")
            read = subprocess.run([str(ROOT / '.isuscope/benchmark.sh'), '--read', str(output)], capture_output=True,
                                  text=True, env=dict(os.environ, BENCHMARK_CONFIG_FILE=str(config)))
            self.assertEqual(read.returncode, 0, read.stderr)
            self.assertIn('"score":4321', read.stdout)
            self.assertIn('"pass":true', read.stdout)

    def test_a_failed_or_errored_job_exits_nonzero_with_passed_false(self):
        self.server.state['replies'] = [job(7, 4, result=result(False, 0, 'payment error\n', 'critical error'))]
        failed = self.bench('run', '--target-id', '11')
        self.assertEqual(failed.returncode, 1)
        self.assertIn('ISUXPORTAL reason: critical error', failed.stdout)
        self.assertEqual(failed.stdout.splitlines()[-1], 'ISUXPORTAL job=7 status=FINISHED passed=false score=0')
        self.server.state.update(replies=[job(7, 2)], polls=0)
        errored = self.bench('run', '--target-id', '11')
        self.assertEqual(errored.returncode, 1)
        self.assertEqual(errored.stdout.splitlines()[-1], 'ISUXPORTAL job=7 status=ERRORED passed=false score=0')

    def test_a_job_that_does_not_finish_in_time_is_reported_as_timeout(self):
        self.server.state['replies'] = [job(7, 1)]
        late = self.bench('run', '--target-id', '11', '--timeout', '0.2')
        self.assertEqual(late.returncode, 1)
        self.assertIn('keeps running on the portal', late.stdout)
        self.assertEqual(late.stdout.splitlines()[-1], 'ISUXPORTAL job=7 status=TIMEOUT passed=false score=0')

    def test_a_missing_or_expired_session_is_reported_without_a_result_line(self):
        expired = self.bench('run', '--target-id', '11', cookie='other=1')
        self.assertEqual(expired.returncode, 2)
        self.assertNotIn('ISUXPORTAL job=', expired.stdout)
        self.assertIn('portal-cookie.py', expired.stderr)
        empty = self.bench('probe', cookie='')
        self.assertEqual(empty.returncode, 2)
        self.assertIn('PORTAL_COOKIE is empty', empty.stderr)

    def test_portal_errors_carry_their_message(self):
        unknown = self.bench('run', '--target-id', '99')
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("Couldn't find ContestantInstance", unknown.stderr)
        self.server.state['closed'] = True
        closed = self.bench('probe')
        self.assertEqual(closed.returncode, 2)
        self.assertIn('contest is not open', closed.stderr)

    def test_probe_and_targets_read_without_enqueueing(self):
        probe = self.bench('probe', '--target-id', '12')
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertIn('team 3 (team-a), 2 servers', probe.stdout)
        self.assertNotIn('enqueued', self.server.state)
        self.assertEqual(self.bench('probe', '--target-id', '99').returncode, 1)
        targets = self.bench('targets')
        self.assertIn('id=11 number=1 public=203.0.113.1 private=10.0.0.1 status=RUNNING', targets.stdout)

    def test_show_prints_one_job(self):
        self.server.state['replies'] = [job(5, 4, result=result(True, 10, 'done\n', ''))]
        shown = self.bench('show', '5')
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(shown.stdout.splitlines(), ['done', 'ISUXPORTAL job=5 status=FINISHED passed=true score=10'])

    def test_protobuf_reading_keeps_negative_ints_and_skips_unknown_fields(self):
        message = bench.Message(field(4, -2) + field(30, b'\x01\x02') + b'\x0d\x00\x00\x00\x00' + field(2, 'x'))
        self.assertEqual(message.int(4), -2)
        self.assertEqual(message.str(2), 'x')
        self.assertEqual(message.int(99), 0)
        with self.assertRaises(ValueError):
            bench.parse(field(2, 'abc')[:-1])


class PollingTest(unittest.TestCase):
    """The job is polled slowly, and every second from shortly before the time the last job took."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location('isuxportal_bench', SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.clock = 0.0
        self.sleeps = []
        for name, fake in (('monotonic', lambda: self.clock), ('sleep', self.sleep)):
            patcher = mock.patch.object(self.module.time, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.clock += seconds

    def portal(self, done_at):
        test = self

        class Portal:
            def job(self, job_id):
                return {'id': job_id, 'status': 'FINISHED' if test.clock >= done_at else 'RUNNING'}
        self.module.job_result = lambda job: {**job, 'score': 1, 'passed': True, 'target': 1, 'stdout': '', 'reason': ''}
        return Portal()

    def test_polls_every_second_only_near_the_end(self):
        result = self.module.wait(self.portal(61.5), 7, 3, 0, fast_seconds=1, fast_after=50)
        self.assertEqual(result['status'], 'FINISHED')
        self.assertEqual(self.sleeps[:17], [3] * 17)  # 0..51s
        self.assertEqual(set(self.sleeps[17:]), {1})
        self.assertLessEqual(self.clock - 61.5, 1, 'seen within a second of the end')
        self.assertLess(len(self.sleeps), 30, 'not every second for the whole run')

    def test_without_a_previous_time_it_polls_at_the_slow_pace(self):
        self.module.wait(self.portal(10), 7, 3, 0, fast_seconds=1, fast_after=None)
        self.assertEqual(set(self.sleeps), {3})

    def test_the_time_of_a_finished_job_is_kept_for_the_next_run(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ['PORTAL_JOB_SECONDS_FILE'] = str(Path(directory) / 'seconds')
            try:
                self.assertIsNone(self.module.last_job_seconds())
                self.module.remember_job_seconds(71.66)
                self.assertEqual(self.module.last_job_seconds(), 71.7)
            finally:
                del os.environ['PORTAL_JOB_SECONDS_FILE']


if __name__ == '__main__':
    unittest.main()
