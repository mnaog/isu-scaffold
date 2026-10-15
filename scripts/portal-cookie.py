#!/usr/bin/env python3
"""Copy the portal's session cookie from the debugging Chrome into .local/benchmark-secrets.env.

  portal-cookie.py [--portal URL] [--cdp http://127.0.0.1:9222] [--secrets PATH] [--check]

Sign in to the portal in the Chrome started with --remote-debugging-port (the one the Playwright
MCP attaches to). This asks that Chrome over the DevTools protocol (Storage.getCookies on the
browser target named by /json/version) for the cookies a request to the portal URL would carry,
and writes them as `export PORTAL_COOKIE='name=value; ...'` into the root repository's
.local/benchmark-secrets.env (a worktree writes to the root's file, which the benchmark adapter
sources), replacing an earlier PORTAL_COOKIE line and keeping the file at mode 0600. Cookie
values are never printed, only their names. --check reports what it found without writing.

Run it again when scripts/isuxportal-bench.py says the session expired. Standard library only:
the DevTools websocket is a minimal RFC 6455 client (masked text frames out; fragmented, ping
and close frames in).
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
COOKIE_LINE = re.compile(r'^\s*(?:export\s+)?PORTAL_COOKIE=')


def root_repository(root=ROOT):
    """The main worktree, so a worktree's session writes the secrets the root's adapter reads."""
    try:
        common = subprocess.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'], cwd=root,
                                capture_output=True, text=True, check=True).stdout.strip()
        return Path(common).parent
    except (OSError, subprocess.CalledProcessError):
        return Path(root)


class WebSocket:
    """Just enough RFC 6455 for one DevTools request and its answer."""

    def __init__(self, url, timeout):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != 'ws':
            raise OSError(f'unsupported DevTools websocket URL: {url}')
        self.sock = socket.create_connection((parts.hostname, parts.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = (parts.path or '/') + (f'?{parts.query}' if parts.query else '')
        self.sock.sendall((f'GET {path} HTTP/1.1\r\nHost: {parts.netloc}\r\nUpgrade: websocket\r\n'
                           f'Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        self.buffer = b''
        while b'\r\n\r\n' not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError('DevTools closed the connection during the websocket handshake')
            self.buffer += chunk
        head, self.buffer = self.buffer.split(b'\r\n\r\n', 1)
        lines = head.decode('latin-1').split('\r\n')
        if ' 101 ' not in lines[0] + ' ':
            raise OSError(f'DevTools refused the websocket: {lines[0]}')
        headers = {name.strip().lower(): value.strip() for name, _, value in (line.partition(':') for line in lines[1:])}
        expected = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        if headers.get('sec-websocket-accept') != expected:
            raise OSError('DevTools answered the websocket handshake with a wrong accept key')

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def send(self, opcode, payload):
        header = bytearray([0x80 | opcode])
        size = len(payload)
        if size < 126:
            header.append(0x80 | size)
        elif size < 1 << 16:
            header += bytes([0x80 | 126]) + size.to_bytes(2, 'big')
        else:
            header += bytes([0x80 | 127]) + size.to_bytes(8, 'big')
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def read(self, size):
        while len(self.buffer) < size:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError('DevTools closed the connection')
            self.buffer += chunk
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        return data

    def receive(self):
        """One whole text message; answers pings and joins continuation frames."""
        parts = []
        while True:
            first, second = self.read(2)
            fin, opcode = first & 0x80, first & 0x0f
            size = second & 0x7f
            if size == 126:
                size = int.from_bytes(self.read(2), 'big')
            elif size == 127:
                size = int.from_bytes(self.read(8), 'big')
            mask = self.read(4) if second & 0x80 else None
            payload = self.read(size)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x9:
                self.send(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                raise OSError('DevTools closed the websocket')
            parts.append(payload)
            if fin:
                return b''.join(parts).decode('utf-8')

    def call(self, method, params=None):
        self.send(0x1, json.dumps({'id': 1, 'method': method, 'params': params or {}}).encode())
        while True:
            message = json.loads(self.receive())
            if message.get('id') == 1:
                if 'error' in message:
                    raise OSError(f'{method}: {message["error"].get("message", message["error"])}')
                return message.get('result', {})


def browser_cookies(cdp, timeout):
    with urllib.request.urlopen(cdp.rstrip('/') + '/json/version', timeout=timeout) as response:
        target = json.load(response).get('webSocketDebuggerUrl')
    if not target:
        raise OSError(f'{cdp}/json/version has no webSocketDebuggerUrl')
    socket_ = WebSocket(target, timeout)
    try:
        return socket_.call('Storage.getCookies').get('cookies', [])
    finally:
        socket_.close()


def for_url(cookies, url, now=None):
    """The cookies a browser would send to url: domain, path, Secure and expiry as RFC 6265 has them."""
    parts = urllib.parse.urlsplit(url)
    host, path = (parts.hostname or '').lower(), parts.path or '/'
    now = time.time() if now is None else now
    chosen = []
    for cookie in cookies:
        domain = str(cookie.get('domain', '')).lower()
        if domain.startswith('.'):
            if host != domain[1:] and not host.endswith(domain):
                continue
        elif host != domain:
            continue
        cookie_path = cookie.get('path') or '/'
        if not (path == cookie_path or path.startswith(cookie_path.rstrip('/') + '/') or cookie_path == '/'):
            continue
        if cookie.get('secure') and parts.scheme != 'https':
            continue
        expires = cookie.get('expires', -1)
        if isinstance(expires, (int, float)) and 0 < expires < now:
            continue
        chosen.append(cookie)
    chosen.sort(key=lambda c: -len(c.get('path') or '/'))
    return chosen


def write_secret(path, value):
    """Replace PORTAL_COOKIE in the secrets file, which stays readable by its owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [line for line in lines if not COOKIE_LINE.match(line)]
    lines.append('export PORTAL_COOKIE=' + shlex.quote(value))
    temporary = path.with_name(path.name + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as out:
        out.write('\n'.join(lines) + '\n')
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--portal', default=os.environ.get('PORTAL_URL', ''), help='portal base URL (PORTAL_URL)')
    parser.add_argument('--cdp', default=os.environ.get('CHROME_CDP_URL', 'http://127.0.0.1:9222'),
                        help='the debugging Chrome (CHROME_CDP_URL)')
    parser.add_argument('--secrets', type=Path, help='default: <root repository>/.local/benchmark-secrets.env')
    parser.add_argument('--check', action='store_true', help='report the cookies found; write nothing')
    parser.add_argument('--timeout', type=float, default=10)
    args = parser.parse_args(argv)
    if not re.match(r'^https?://[^/]+', args.portal):
        parser.error('--portal (or PORTAL_URL) must be the portal\'s http(s) base URL')
    try:
        cookies = for_url(browser_cookies(args.cdp, args.timeout), args.portal)
    except (OSError, ValueError) as error:
        print(f'portal-cookie: cannot read cookies from {args.cdp} (start Chrome with '
              f'--remote-debugging-port): {error}', file=sys.stderr)
        return 2
    if not cookies:
        print(f'portal-cookie: Chrome has no cookie for {args.portal}; sign in to the portal there first',
              file=sys.stderr)
        return 1
    names = ', '.join(cookie['name'] for cookie in cookies)
    if args.check:
        print(f'{len(cookies)} cookies for {args.portal}: {names}')
        return 0
    secrets = args.secrets or root_repository() / '.local/benchmark-secrets.env'
    write_secret(secrets, '; '.join(f'{c["name"]}={c.get("value", "")}' for c in cookies))
    print(f'wrote PORTAL_COOKIE ({len(cookies)} cookies: {names}) to {secrets}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
