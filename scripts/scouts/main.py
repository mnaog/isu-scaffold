#!/usr/bin/env python3
"""Scouts: Claude Code, Codex and Kimi run in fresh sessions and save a report of up to 300 characters.

Each scout starts again interval_seconds after its last report was saved. The latest reports go to
docs/scout-board.md, which the main session reads while a benchmark runs (scripts/tool-guard.py --post).
`make scout-start` / `scout-stop` / `scout-status` run it from the main worktree.
"""
import argparse
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import threading
from inputs import current, generate
from runner import active, attempt, daemon, export_board, initialize, start, stop_daemon
from store import config, connect, local, root, rows


def status(repo):
    with connect(repo) as db:
        return {'daemon_running': active(repo), 'scouts': rows(db, 'SELECT * FROM scouts ORDER BY name'),
                'conversations': rows(db, 'SELECT * FROM conversation_sources ORDER BY agent')}

def main():
    parser = argparse.ArgumentParser(description='scouts that post short reports for the main session')
    sub = parser.add_subparsers(dest='command', required=True)
    for cmd in ('init', 'db-path', 'start', 'stop', 'status', 'daemon', 'export', 'input', 'current', 'check'):
        sub.add_parser(cmd)
    p = sub.add_parser('conversation')
    p.add_argument('agent', choices=['claude', 'codex'])
    p.add_argument('session_id')
    p = sub.add_parser('once')
    p.add_argument('name')
    p.add_argument('--smoke', action='store_true', help='接続のみ検証し、実行ログだけを保存。運用boardへ投稿しない')
    args = parser.parse_args()
    repo = root()
    cfg = config(repo)
    if args.command == 'current':
        print(json.dumps(current(repo, cfg), ensure_ascii=False, indent=2))
        return 0
    initialize(repo, cfg)
    if args.command == 'db-path':
        print(local(repo) / 'state.sqlite3')
    elif args.command == 'init':
        export_board(repo)
        print(local(repo) / 'state.sqlite3')
    elif args.command == 'conversation':
        if not args.session_id.strip():
            raise ValueError('実際のセッションIDが必要です')
        with connect(repo) as db:
            db.execute('INSERT INTO conversation_sources(agent,session_id) VALUES (?,?) ON CONFLICT(agent) DO UPDATE SET session_id=excluded.session_id,registered_at=strftime(\'%s\',\'now\')', (args.agent, args.session_id))
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
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
