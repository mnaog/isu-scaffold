import base64
import hashlib
import json
import os
from pathlib import Path
import socketserver
import stat
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts/portal-cookie.py'
GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
COOKIES = [
    {'name': '_isuxportal_session', 'value': 'SECRET-1', 'domain': 'portal.example', 'path': '/', 'secure': True,
     'expires': -1},
    {'name': 'shared', 'value': 'SECRET-2', 'domain': '.example', 'path': '/', 'secure': False, 'expires': -1},
    {'name': 'elsewhere', 'value': 'nope', 'domain': 'other.example', 'path': '/', 'secure': False, 'expires': -1},
    {'name': 'admin', 'value': 'nope', 'domain': 'portal.example', 'path': '/admin', 'secure': False, 'expires': -1},
    {'name': 'old', 'value': 'nope', 'domain': 'portal.example', 'path': '/', 'secure': False, 'expires': 1},
]


def frame(opcode, payload, fin=True):
    head = bytes([(0x80 if fin else 0) | opcode])
    size = len(payload)
    if size < 126:
        head += bytes([size])
    elif size < 1 << 16:
        head += bytes([126]) + size.to_bytes(2, 'big')
    else:
        head += bytes([127]) + size.to_bytes(8, 'big')
    return head + payload


class FakeDevTools(socketserver.StreamRequestHandler):
    """/json/version over HTTP, then the browser websocket: a ping and a fragmented answer."""

    def read_frame(self):
        first, second = self.rfile.read(2)
        size = second & 0x7f
        if size == 126:
            size = int.from_bytes(self.rfile.read(2), 'big')
        elif size == 127:
            size = int.from_bytes(self.rfile.read(8), 'big')
        mask = self.rfile.read(4)
        assert second & 0x80, 'a client frame must be masked'
        return first & 0x0f, bytes(b ^ mask[i % 4] for i, b in enumerate(self.rfile.read(size)))

    def handle(self):
        request = self.rfile.readline().decode()
        headers = {}
        while True:
            line = self.rfile.readline().decode().strip()
            if not line:
                break
            name, _, value = line.partition(':')
            headers[name.strip().lower()] = value.strip()
        port = self.server.server_address[1]
        if request.startswith('GET /json/version'):
            body = json.dumps({'webSocketDebuggerUrl': f'ws://127.0.0.1:{port}/devtools/browser/abc'}).encode()
            self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
                             + str(len(body)).encode() + b'\r\nConnection: close\r\n\r\n' + body)
            return
        accept = base64.b64encode(hashlib.sha1((headers['sec-websocket-key'] + GUID).encode()).digest()).decode()
        self.wfile.write(f'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                         f'Sec-WebSocket-Accept: {accept}\r\n\r\n'.encode())
        opcode, payload = self.read_frame()
        call = json.loads(payload)
        self.server.calls.append(call['method'])
        self.wfile.write(frame(0x9, b'hi'))
        pong = self.read_frame()
        self.server.pongs.append(pong)
        self.wfile.write(frame(0x1, json.dumps({'method': 'Target.event', 'params': {}}).encode()))
        answer = json.dumps({'id': call['id'], 'result': {'cookies': COOKIES}}).encode()
        middle = len(answer) // 2
        self.wfile.write(frame(0x1, answer[:middle], fin=False) + frame(0x0, answer[middle:]))


class PortalCookieTest(unittest.TestCase):
    def setUp(self):
        self.server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), FakeDevTools)
        self.server.daemon_threads = True
        self.server.calls, self.server.pongs = [], []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.cdp = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.tmp = tempfile.TemporaryDirectory()
        self.secrets = Path(self.tmp.name) / '.local/benchmark-secrets.env'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def cookie(self, *args, portal='https://portal.example/'):
        return subprocess.run(['python3', str(SCRIPT), '--portal', portal, '--cdp', self.cdp,
                               '--secrets', str(self.secrets), *args], capture_output=True, text=True, timeout=30)

    def test_it_writes_the_cookies_a_request_to_the_portal_would_carry(self):
        self.secrets.parent.mkdir()
        self.secrets.write_text("export BENCHMARK_TOKEN=keep\nexport PORTAL_COOKIE='stale=1'\n")
        done = self.cookie()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.server.calls, ['Storage.getCookies'])
        self.assertEqual(self.server.pongs, [(0xA, b'hi')])
        self.assertEqual(self.secrets.read_text(),
                         "export BENCHMARK_TOKEN=keep\nexport PORTAL_COOKIE='_isuxportal_session=SECRET-1; shared=SECRET-2'\n")
        self.assertEqual(stat.S_IMODE(self.secrets.stat().st_mode), 0o600)
        self.assertIn('_isuxportal_session, shared', done.stdout)
        self.assertNotIn('SECRET', done.stdout + done.stderr)
        sourced = subprocess.run(['bash', '-c', f'source {self.secrets}; printf %s "$PORTAL_COOKIE"'],
                                 capture_output=True, text=True)
        self.assertEqual(sourced.stdout, '_isuxportal_session=SECRET-1; shared=SECRET-2')

    def test_plain_http_leaves_secure_cookies_out_and_check_writes_nothing(self):
        checked = self.cookie('--check', portal='http://portal.example')
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn('1 cookies for http://portal.example: shared', checked.stdout)
        self.assertFalse(self.secrets.exists())

    def test_no_cookie_or_no_chrome_is_reported(self):
        missing = self.cookie(portal='https://nowhere.test')
        self.assertEqual(missing.returncode, 1)
        self.assertIn('sign in to the portal', missing.stderr)
        self.server.shutdown()
        self.server.server_close()
        gone = self.cookie()
        self.assertEqual(gone.returncode, 2)
        self.assertIn('--remote-debugging-port', gone.stderr)
        self.server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), FakeDevTools)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


if __name__ == '__main__':
    unittest.main()
