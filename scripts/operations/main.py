#!/usr/bin/env python3
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import sys
import threading
import time
from inputs import generate, metrics
from runner import active, attempt, daemon, export_board, initialize, start, stop_daemon
from store import config, connect, local, root, rows, worker_sql


def status(repo):
    with connect(repo) as db:
        return {'daemon_running': active(repo), 'workers': rows(db, 'SELECT * FROM workers ORDER BY started_at DESC'),
                'worker_updates': rows(db, 'SELECT * FROM worker_updates ORDER BY id DESC LIMIT 100'),
                'scouts': rows(db, 'SELECT * FROM scouts ORDER BY name'),
                'operators': rows(db, 'SELECT * FROM operators'),
                'processes': rows(db, 'SELECT * FROM worker_processes ORDER BY started_at DESC LIMIT 100')}


def serve(repo, port):
    cached = {'value': {'error': '計測を取得中です'}}
    shutdown = threading.Event()
    def refresh():
        while not shutdown.is_set():
            try:
                cached['value'] = metrics(repo, config(repo), shutdown)
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
            if self.path == '/':
                body = Path(__file__).with_name('board.html').read_bytes()
                content_type = 'text/html; charset=utf-8'
            elif self.path == '/api/board':
                result = status(repo)
                # Local execution paths/logs are not part of the human board.
                for scout in result['scouts']:
                    scout.pop('input_ref', None)
                result['metrics'] = cached['value']
                body = json.dumps(result, ensure_ascii=False).encode()
                content_type = 'application/json; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
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
    parser = argparse.ArgumentParser(description='scaffold local operations; worker records accept SQL on stdin')
    sub = parser.add_subparsers(dest='command', required=True)
    for cmd in ('init', 'db-path', 'sql', 'start', 'stop', 'status', 'daemon', 'export', 'input', 'check'):
        sub.add_parser(cmd)
    p = sub.add_parser('operator')
    p.add_argument('agent', choices=['claude', 'codex'])
    p.add_argument('session_id')
    p = sub.add_parser('once')
    p.add_argument('name')
    p.add_argument('--smoke', action='store_true', help='接続のみ検証し、実行ログだけを保存。運用boardへ投稿しない')
    p = sub.add_parser('board')
    p.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    repo = root()
    cfg = config(repo)
    initialize(repo, cfg)
    if args.command == 'db-path':
        print(local(repo) / 'state.sqlite3')
    elif args.command == 'init':
        export_board(repo)
        print(local(repo) / 'state.sqlite3')
    elif args.command == 'sql':
        print(json.dumps(worker_sql(repo, Path.cwd(), sys.stdin.read()), ensure_ascii=False, indent=2))
    elif args.command == 'operator':
        if not args.session_id.strip():
            raise ValueError('実際のセッションIDが必要です')
        with connect(repo) as db:
            db.execute('INSERT INTO operators(agent,session_id) VALUES (?,?) ON CONFLICT(agent) DO UPDATE SET session_id=excluded.session_id,registered_at=strftime(\'%s\',\'now\')', (args.agent, args.session_id))
    elif args.command == 'start':
        if Path.cwd().resolve() != repo:
            raise ValueError('scout開始はmain worktreeのrootで行ってください')
        print(f'scout daemon PID {start(repo)}')
    elif args.command == 'stop':
        stop_daemon(repo)
        print('scout daemon stopped')
    elif args.command == 'daemon':
        daemon(repo)
    elif args.command == 'status':
        print(json.dumps(status(repo), ensure_ascii=False, indent=2))
    elif args.command == 'export':
        export_board(repo)
    elif args.command == 'input':
        path = local(repo) / 'input-preview.json'
        path.write_text(json.dumps(generate(repo, cfg), ensure_ascii=False, indent=2))
        print(path)
    elif args.command == 'check':
        for name, spec in cfg['scouts'].items():
            print(json.dumps({'scout': name, 'executable': shutil.which(spec['argv'][0]), 'model': spec['model'], 'unavailable': spec.get('unavailable'), 'authentication': 'once --smokeで確認'}, ensure_ascii=False))
    elif args.command == 'once':
        if args.name not in cfg['scouts']:
            raise ValueError('unknown scout')
        if args.smoke:
            from runner import invoke
            from store import lock
            import uuid
            spec = cfg['scouts'][args.name]
            if spec.get('unavailable') or not spec.get('model'):
                raise ValueError(spec.get('unavailable') or 'model未設定')
            with lock(repo, 'scout-' + args.name) as held:
                session = str(uuid.uuid4())
                path = local(repo) / 'smoke' / args.name / session
                path.mkdir(parents=True)
                text = (repo / 'docs/roles/scout-prompt.txt').read_text() + '\n今回は接続確認です。探索・ツール操作は不要です。「接続確認済み」とだけ回答してください。'
                report = invoke(repo, spec, text, path, threading.Event(), cfg['timeout_seconds'], session, lock_fd=held.fileno())
                if not 1 <= len(report.strip()) <= 300:
                    raise ValueError('接続応答の文字数違反')
                print(json.dumps({'scout': args.name, 'report': report.strip(), 'log': str(path)}, ensure_ascii=False))
        elif not attempt(repo, cfg, args.name):
            print(json.dumps(status(repo)['scouts'], ensure_ascii=False), file=sys.stderr)
            return 1
    elif args.command == 'board':
        serve(repo, args.port)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
