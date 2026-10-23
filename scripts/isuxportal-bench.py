#!/usr/bin/env python3
"""Run one benchmark on an isuxportal portal (the ISUCON portal since ISUCON10) as a contestant.

  isuxportal-bench.py run --portal URL --target-id ID     enqueue, wait, print the result
  isuxportal-bench.py probe --portal URL [--target-id ID] sign-in and contest check; enqueues nothing
  isuxportal-bench.py targets --portal URL                 the team's servers and their ids
  isuxportal-bench.py show JOB_ID --portal URL             one job and its output

The contestant API speaks protobuf over HTTP with the browser's session cookie, the same calls the
portal's own page makes (portal/app/javascript/ApiClient.ts in isucon/isucon14-portal):
GET /api/session lists the team's servers (contestant_instances), POST
/api/contestant/benchmark_jobs enqueues a job for one of them, and GET
/api/contestant/benchmark_jobs/:id returns it with its result. A write needs the Rails CSRF
token, which this reads from a portal page's <meta name="csrf-token">, keeping the cookies the
portal sets meanwhile.

The cookie comes from PORTAL_COOKIE (a Cookie header value). scripts/portal-cookie.py copies it
from the browser into .local/benchmark-secrets.env, which the benchmark adapter sources. The
portal and the target are flags, so the benchmark contract (scripts/benchmark-contract.py) binds
them and a new cookie needs no reseal; PORTAL_URL and PORTAL_TARGET_ID are fallbacks for use by
hand.

`run` and `show` print the job's contestant-visible output and end with the one line the adapter
reads (BENCHMARK_SCORE_REGEX='^ISUXPORTAL .*score=[0-9]+', PASS/FAIL on passed=true/false):

  ISUXPORTAL job=<id> status=<FINISHED|ERRORED|CANCELLED|TIMEOUT> passed=<true|false> score=<n>

`run` polls the job every --poll-seconds (3) and, from --fast-before (10) seconds before the time the
last job took, every --fast-poll-seconds (1): the result is seen about a second after it is out
without asking the portal every second for the whole run. The time is kept in
.local/portal-job-seconds (PORTAL_JOB_SECONDS_FILE).

They exit 0 only for a finished, passed job; 1 for any other result; 2 when the portal could not
be used (no job, no final line). A contestant cannot cancel a job, so stopping this script
(scripts/benchmark-stop.sh, the adapter's timeout) leaves the job running on the portal.
Only the fields used here are decoded; field numbers follow proto/isuxportal of the portal.
"""
import argparse
import html
import os
from pathlib import Path
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROTOBUF = 'application/vnd.google.protobuf'
ACCEPT = 'application/protobuf, application/vnd.google.protobuf, text/plain'
USER_AGENT = 'isu-scaffold isuxportal-bench'
# isuxportal.proto.resources.BenchmarkJob.Status / ContestantInstance.Status
JOB_STATUS = {0: 'PENDING', 1: 'RUNNING', 2: 'ERRORED', 3: 'CANCELLED', 4: 'FINISHED'}
DONE = {'ERRORED', 'CANCELLED', 'FINISHED'}
INSTANCE_STATUS = {0: 'UNKNOWN', 1: 'PENDING', 2: 'MODIFYING', 3: 'STOPPED', 4: 'RUNNING', 5: 'TERMINATED'}
CSRF_META = re.compile(r'<meta\s+(?:name="csrf-token"\s+content="([^"]*)"|content="([^"]*)"\s+name="csrf-token")')
SIGN_IN_HINT = ('sign in to the portal in the debugging Chrome, then refresh the cookie: '
                'python3 scripts/portal-cookie.py --portal URL')


class PortalError(Exception):
    pass


class Stopped(Exception):
    pass


# --- protobuf: just enough for the messages used here --------------------------------------
def varint(value):
    value &= (1 << 64) - 1
    out = bytearray()
    while True:
        byte, value = value & 0x7f, value >> 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def field(number, value):
    """One encoded field: an int as a varint, str or bytes (a nested message) length-delimited."""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    if isinstance(value, str):
        value = value.encode()
    return varint(number << 3 | 2) + varint(len(value)) + value


def parse(data):
    """{field number: [raw values]}: varints as int, length-delimited as bytes; unknown fields kept."""
    fields, i = {}, 0

    def read_varint():
        nonlocal i
        result = shift = 0
        while True:
            if i >= len(data) or shift > 63:
                raise ValueError('malformed protobuf varint')
            byte = data[i]
            i += 1
            result |= (byte & 0x7f) << shift
            if not byte & 0x80:
                return result
            shift += 7

    while i < len(data):
        key = read_varint()
        number, wire = key >> 3, key & 7
        if wire == 0:
            value = read_varint()
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value, i = data[i:i + size], i + size
        elif wire == 2:
            size = read_varint()
            value, i = data[i:i + size], i + size
        else:
            raise ValueError(f'unsupported protobuf wire type {wire}')
        if i > len(data):
            raise ValueError('truncated protobuf message')
        fields.setdefault(number, []).append(value)
    return fields


class Message:
    """Read access to a decoded message; a missing field reads as the proto3 default."""

    def __init__(self, data=b''):
        self.fields = parse(data or b'')

    def int(self, number):
        values = self.fields.get(number)
        value = values[-1] if values and isinstance(values[-1], int) else 0
        return value - (1 << 64) if value >= 1 << 63 else value

    def bool(self, number):
        return bool(self.int(number))

    def str(self, number):
        values = self.fields.get(number)
        return values[-1].decode('utf-8', 'replace') if values and isinstance(values[-1], bytes) else ''

    def message(self, number):
        values = self.fields.get(number)
        return Message(values[-1]) if values and isinstance(values[-1], bytes) else None

    def messages(self, number):
        return [Message(value) for value in self.fields.get(number, []) if isinstance(value, bytes)]


def job_result(job):
    """BenchmarkJob (resources/benchmark_job.proto) with its BenchmarkResult (benchmark_result.proto)."""
    result = job.message(18)
    execution = result.message(5) if result else None
    return {
        'id': job.int(1),
        'target': job.int(3),
        'status': JOB_STATUS.get(job.int(4), str(job.int(4))),
        'score': result.int(3) if result else job.int(9),
        'passed': bool(result and result.bool(2)),
        'reason': execution.str(1) if execution else '',
        'stdout': execution.str(2) if execution else '',
    }


def instances(session):
    """GetCurrentSessionResponse (services/common/me.proto) field 5: ContestantInstance list."""
    return [{'id': item.int(7), 'number': item.int(3), 'public': item.str(4), 'private': item.str(5),
             'status': INSTANCE_STATUS.get(item.int(6), str(item.int(6)))} for item in session.messages(5)]


# --- HTTP ---------------------------------------------------------------------------------------
class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect from the API means the portal wants a sign-in; report it instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Portal:
    def __init__(self, url, cookie, timeout):
        self.base = url.rstrip('/')
        self.cookies = {}
        for part in cookie.split(';'):
            name, sep, value = part.strip().partition('=')
            if sep and name:
                self.cookies[name] = value
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirect())
        self.csrf = None

    def remember(self, headers):
        for header in (headers.get_all('Set-Cookie') or []) if headers is not None else []:
            name, sep, value = header.split(';', 1)[0].strip().partition('=')
            if sep and name:
                self.cookies[name] = value

    def request(self, method, path, body=None, page=False):
        headers = {'Accept': 'text/html' if page else ACCEPT, 'User-Agent': USER_AGENT}
        if body is not None:
            # First: reading the token can change the session cookie the write must carry.
            headers['X-Csrf-Token'] = self.csrf_token()
            headers['Content-Type'] = PROTOBUF
        if self.cookies:
            headers['Cookie'] = '; '.join(f'{name}={value}' for name, value in self.cookies.items())
        request = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                self.remember(response.headers)
                return response.read()
        except urllib.error.HTTPError as error:
            self.remember(error.headers)
            raise PortalError(f'{method} {path}: {describe(error)}') from None
        except (urllib.error.URLError, OSError) as error:
            raise PortalError(f'cannot reach {self.base}: {getattr(error, "reason", error)}') from None

    def csrf_token(self):
        """The token the portal's page sends as X-Csrf-Token (Rails csrf_meta_tags)."""
        if self.csrf is None:
            for path in ('/contestant', '/'):
                try:
                    page = self.request('GET', path, page=True).decode('utf-8', 'replace')
                except PortalError:
                    continue
                found = CSRF_META.search(page)
                if found:
                    self.csrf = html.unescape(found.group(1) or found.group(2))
                    break
            else:
                raise PortalError('no csrf-token in the portal pages (/contestant, /); ' + SIGN_IN_HINT)
        return self.csrf

    def session(self):
        session = Message(self.request('GET', '/api/session'))
        if session.message(2) is None:
            raise PortalError('the cookie is not a contestant session; ' + SIGN_IN_HINT)
        return session

    def job(self, job_id):
        job = Message(self.request('GET', f'/api/contestant/benchmark_jobs/{int(job_id)}')).message(1)
        if job is None:
            raise PortalError(f'the portal returned no job {job_id}')
        return job

    def enqueue(self, target_id):
        job = Message(self.request('POST', '/api/contestant/benchmark_jobs', field(1, int(target_id)))).message(1)
        if job is None:
            raise PortalError('the portal returned no job for the enqueue')
        return job


def describe(error):
    """isuxportal answers API errors with an isuxportal.proto.Error (error.proto)."""
    status = error.code
    if 300 <= status < 400:
        return f'HTTP {status} redirect to {error.headers.get("Location", "?")}: not signed in; {SIGN_IN_HINT}'
    body = error.read() if error.fp else b''
    content_type = error.headers.get('Content-Type', '') if error.headers else ''
    if 'protobuf' in content_type:
        try:
            remote = Message(body)
            details = [remote.str(2), remote.str(3)] + [d.decode('utf-8', 'replace') for d in remote.fields.get(4, [])
                                                       if isinstance(d, bytes)]
            text = ': '.join(part for part in details if part)
        except ValueError:
            text = ''
    else:
        text = ' '.join(body.decode('utf-8', 'replace').split())[:200]
    hint = f'; {SIGN_IN_HINT}' if status in (401, 403) else ''
    return f'HTTP {status} {text}'.rstrip() + hint


# --- commands -------------------------------------------------------------------------------------
def final_line(result):
    return (f'ISUXPORTAL job={result["id"]} status={result["status"]} '
            f'passed={"true" if result["passed"] else "false"} score={result["score"]}')


def report(result):
    """The job's own output, then the line the adapter reads. Returns the exit code."""
    if result['stdout']:
        sys.stdout.write(result['stdout'] if result['stdout'].endswith('\n') else result['stdout'] + '\n')
    if result['reason']:
        print('ISUXPORTAL reason: ' + ' '.join(result['reason'].split()))
    if result['status'] != 'FINISHED':
        result = dict(result, passed=False)
    print(final_line(result), flush=True)
    return 0 if result['status'] == 'FINISHED' and result['passed'] else 1


def wait(portal, job_id, poll_seconds, timeout, fast_seconds=None, fast_after=None):
    """Poll until the job is done; a few failed polls in a row are tolerated (the bench keeps going).
    After `fast_after` seconds (near the time the last job took), poll every `fast_seconds`."""
    started = time.monotonic()
    deadline = started + timeout if timeout > 0 else None
    status, failures = None, 0
    while True:
        try:
            result = job_result(portal.job(job_id))
            failures = 0
        except PortalError as error:
            failures += 1
            if failures >= 5:
                raise
            print(f'isuxportal-bench: poll failed ({failures}/5): {error}', file=sys.stderr, flush=True)
            result = None
        if result:
            if result['status'] != status:
                status = result['status']
                print(f'ISUXPORTAL job={job_id} status={status}', flush=True)
            if status in DONE:
                return result
        if deadline is not None and time.monotonic() >= deadline:
            return {'id': job_id, 'target': 0, 'status': 'TIMEOUT', 'score': 0, 'passed': False,
                    'reason': f'no result within {timeout:g}s; the job keeps running on the portal', 'stdout': ''}
        fast = fast_seconds is not None and fast_after is not None and time.monotonic() - started >= fast_after
        time.sleep(min(poll_seconds, fast_seconds) if fast else poll_seconds)


def job_seconds_file():
    return Path(os.environ.get('PORTAL_JOB_SECONDS_FILE') or Path(__file__).resolve().parent.parent / '.local/portal-job-seconds')


def last_job_seconds():
    try:
        return float(job_seconds_file().read_text().strip())
    except (OSError, ValueError):
        return None


def remember_job_seconds(seconds):
    try:
        path = job_seconds_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'{seconds:.1f}\n')
    except OSError:
        pass  # only the next run's polling depends on it


def command_run(portal, args):
    target = require_target(args)
    job = job_result(portal.enqueue(target))
    print(f'ISUXPORTAL enqueued job={job["id"]} target={target}', flush=True)
    last = last_job_seconds()
    fast_after = max(0.0, last - args.fast_before) if last is not None else None
    started = time.monotonic()
    try:
        result = wait(portal, job['id'], args.poll_seconds, args.timeout, args.fast_poll_seconds, fast_after)
        if result['status'] == 'FINISHED':
            remember_job_seconds(time.monotonic() - started)
    except PortalError as error:
        print(f'isuxportal-bench: {error}', file=sys.stderr)
        result = {'id': job['id'], 'target': target, 'status': 'ERRORED', 'score': 0, 'passed': False,
                  'reason': f'lost the job on the portal: {error}', 'stdout': ''}
    except Stopped:
        print(f'isuxportal-bench: stopped waiting; job {job["id"]} keeps running on the portal', file=sys.stderr)
        return 143
    return report(result)


def command_show(portal, args):
    return report(job_result(portal.job(args.job_id)))


def command_targets(portal, args):
    servers = instances(portal.session())
    if not servers:
        print('no servers: the contest is not open for the team yet, or none are registered')
    for server in servers:
        print(f'id={server["id"]} number={server["number"]} public={server["public"] or "-"} '
              f'private={server["private"] or "-"} status={server["status"]}')
    return 0


def command_probe(portal, args):
    session = portal.session()
    team = session.message(1)
    servers = instances(session)
    # The contestant API itself (contest open, team allowed); reads one job at most, starts nothing.
    portal.request('GET', '/api/contestant/benchmark_jobs?limit=1')
    print(f'signed in as team {team.int(1) if team else "?"} ({team.str(2) if team else ""}), '
          f'{len(servers)} servers: ' + ', '.join(f'id={s["id"]} {s["status"]}' for s in servers))
    target = args.target_id
    if target and int(target) not in {s['id'] for s in servers}:
        print(f'isuxportal-bench: target {target} is not one of the team\'s servers', file=sys.stderr)
        return 1
    return 0


def require_target(args):
    if not args.target_id:
        raise PortalError('--target-id (or PORTAL_TARGET_ID) is required; list them with `targets`')
    return int(args.target_id)


def number(text):
    value = float(text)
    if value < 0:
        raise argparse.ArgumentTypeError('must not be negative')
    return value


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--portal', default=os.environ.get('PORTAL_URL', ''), help='portal base URL (PORTAL_URL)')
    common.add_argument('--poll-seconds', type=number, default=number(os.environ.get('PORTAL_POLL_SECONDS', '3')))
    common.add_argument('--fast-poll-seconds', type=number,
                        default=number(os.environ.get('PORTAL_FAST_POLL_SECONDS', '1')),
                        help='poll this often near the time the last job took')
    common.add_argument('--fast-before', type=number, default=number(os.environ.get('PORTAL_FAST_BEFORE_SECONDS', '10')),
                        help='start polling fast this many seconds before that time')
    common.add_argument('--timeout', type=number, default=number(os.environ.get('PORTAL_TIMEOUT_SECONDS', '1800')),
                        help='give up waiting after this many seconds (0: no limit)')
    common.add_argument('--request-timeout', type=number, default=15)
    target = argparse.ArgumentParser(add_help=False)
    target.add_argument('--target-id', default=os.environ.get('PORTAL_TARGET_ID', ''),
                        help='ContestantInstance id from `targets` (PORTAL_TARGET_ID)')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('run', parents=[common, target]).set_defaults(handler=command_run)
    commands.add_parser('probe', parents=[common, target]).set_defaults(handler=command_probe)
    commands.add_parser('targets', parents=[common]).set_defaults(handler=command_targets)
    show = commands.add_parser('show', parents=[common])
    show.add_argument('job_id', type=int)
    show.set_defaults(handler=command_show)
    args = parser.parse_args(argv)

    if not re.match(r'^https?://[^/]+', args.portal):
        parser.error('--portal (or PORTAL_URL) must be the portal\'s http(s) base URL')
    cookie = os.environ.get('PORTAL_COOKIE', '')
    if not cookie.strip():
        print('isuxportal-bench: PORTAL_COOKIE is empty; ' + SIGN_IN_HINT, file=sys.stderr)
        return 2

    def stop(signum, frame):
        raise Stopped()
    signal.signal(signal.SIGTERM, stop)
    try:
        return args.handler(Portal(args.portal, cookie, args.request_timeout), args)
    except PortalError as error:
        print(f'isuxportal-bench: {error}', file=sys.stderr)
        return 2
    except Stopped:
        print('isuxportal-bench: stopped', file=sys.stderr)
        return 143
    except ValueError as error:
        print(f'isuxportal-bench: unreadable portal response: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
