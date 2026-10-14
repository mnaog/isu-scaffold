#!/usr/bin/env python3
"""The human board: runs, comparisons and load from isuscope, the scouts' reports and the required records.

Read-only and served on loopback. `make board` starts it from the main worktree.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import sys
import threading
from urllib.parse import urlsplit, parse_qs
from activity import activity
from board_data import DetailCache, selection
from reads import ISUSCOPE, metrics, root
import records
from scouts import scouts

CONFIG = {'isuscope': ISUSCOPE}


def serve(repo, port):
    details = DetailCache()
    record_listing = records.ListingCache()
    here = Path(__file__).parent
    mermaid = (here / 'vendor/mermaid.tiny.js').read_bytes()
    # The versioned URL lets the browser keep the 2.8MB bundle until the vendored file changes.
    mermaid_url = f'/mermaid.tiny.js?v={hashlib.sha256(mermaid).hexdigest()[:12]}'
    cached = {'value': {'error': '計測を取得中です'}}
    shutdown = threading.Event()
    def refresh():
        while not shutdown.is_set():
            try:
                cached['value'] = metrics(repo, CONFIG, shutdown)
            except Exception as exc:
                cached['value'] = {'error': str(exc)}
            shutdown.wait(30)
    thread = threading.Thread(target=refresh, daemon=True)
    thread.start()
    def stop_server(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop_server)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path)
            cache_control = 'no-store'
            if path.path == '/api/metrics':
                try:
                    args = selection(parse_qs(path.query, keep_blank_values=True))
                    result = details.get(repo, CONFIG, *args)
                except ValueError as exc:
                    self.send_error(400, str(exc).encode('ascii', 'replace').decode())
                    return
                body = json.dumps(result, ensure_ascii=False).encode()
                content_type = 'application/json; charset=utf-8'
            elif path.path == '/api/records':
                body = json.dumps(record_listing.get(repo), ensure_ascii=False).encode()
                content_type = 'application/json; charset=utf-8'
            elif path.path == '/api/record':
                params = parse_qs(path.query)
                try:
                    if list(params) != ['path'] or len(params['path']) != 1:
                        raise ValueError('give one path')
                    result = records.content(repo, params['path'][0])
                except ValueError as exc:
                    self.send_error(400, str(exc).encode('ascii', 'replace').decode())
                    return
                body = json.dumps(result, ensure_ascii=False).encode()
                content_type = 'application/json; charset=utf-8'
            elif self.path == mermaid_url:
                body = mermaid
                content_type = 'text/javascript; charset=utf-8'
                cache_control = 'public, max-age=31536000, immutable'
            elif self.path in ('/board-workspace.js', '/board-details.js', '/board-insights.js', '/board-scenario.js',
                               '/board-records.js'):
                body = (here / self.path[1:]).read_bytes()
                content_type = 'text/javascript; charset=utf-8'
            elif self.path == '/':
                body = (here / 'board.html').read_bytes().replace(b'src="/mermaid.tiny.js"', f'src="{mermaid_url}"'.encode())
                content_type = 'text/html; charset=utf-8'
            elif self.path == '/api/board':
                result = {'metrics': cached['value']}
                result['activity'] = activity(repo)
                result['scouts'] = scouts(repo)
                body = json.dumps(result, ensure_ascii=False).encode()
                content_type = 'application/json; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', cache_control)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    finally:
        shutdown.set()
        server.server_close()
        thread.join(timeout=35)


def main():
    parser = argparse.ArgumentParser(description='open the human board on 127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    serve(root(), args.port)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
