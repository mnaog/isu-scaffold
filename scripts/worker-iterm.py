#!/usr/bin/env python3
"""Open a visible iTerm tab; run the interactive worker in its foreground."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import uuid
import time

sys.path.insert(0, str(Path(__file__).resolve().parent / 'operations'))
from store import connect, git, local, root

APPLE_SCRIPT = '''on run argv
 tell application "iTerm"
  activate
  if (count of windows) = 0 then
   create window with default profile
  else
   tell current window to create tab with default profile
  end if
  tell current session of current window to write text (item 1 of argv)
 end tell
end run
'''


def save_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False))
    temporary.replace(path)


def started_task(repo, record):
    with connect(repo) as db:
        return db.execute('''SELECT w.task_id,w.session_id FROM workers w
            JOIN worker_processes p ON p.task_id=w.task_id
            WHERE p.process_id=? AND p.pid=? AND p.exited_at IS NULL
              AND p.session_id=w.session_id AND w.worktree=? AND w.parent_session_id=?''',
            (record['process_id'], record['pid'], record['worktree'], record['parent_session_id'])).fetchone()


def wait_started(repo, context_path, timeout, worktree=None, parent=None):
    deadline = time.monotonic() + timeout
    while True:
        if context_path.exists():
            record = json.loads(context_path.read_text())
            if record.get('state') == 'exited':
                raise RuntimeError('worker exited before task registration; inspect its iTerm tab')
            task = started_task(repo, record)
            if task:
                return task
        elif worktree is not None and parent:
            # Existing sessions from before the launch-file change: match explicit
            # worktree + parent + process/session association, never recency.
            with connect(repo) as db:
                tasks = db.execute('''SELECT w.task_id,w.session_id FROM workers w
                    JOIN worker_processes p ON p.task_id=w.task_id AND p.session_id=w.session_id
                    WHERE w.worktree=? AND w.parent_session_id=? AND p.exited_at IS NULL
                    AND w.state IN ('working','blocked','developed')''', (str(worktree), parent)).fetchall()
            if len(tasks) == 1:
                return tasks[0]
        if time.monotonic() >= deadline:
            raise RuntimeError('worker task registration not confirmed; CLI may still be running. Do not launch another worker; inspect the existing iTerm tab and retry --check-started')
        time.sleep(.2)


def worker_prompt(context_path, parent, mode, purpose):
    operation = 'Phase 1の継続改善' if mode == 'phase1' else '通常の1目的タスク。完了後は待機'
    return f'''役割: worker。AGENTS.md、practice.md（存在する場合）、docs/roles/worker.md、.local/lane.mdを読む。
運用は「{operation}」。固定された目的: {purpose}。別目的の依頼へ切り替えない。
親operator実セッションIDは {parent}（起動側が明示した値）。起動情報は {context_path} にも保存済み。
親IDの環境変数が見えなくても推測や人間への再質問は不要。./scripts/worker-dbが起動情報から補完する。
自分の実セッションIDはCODEX_THREAD_IDを使う。作業前にworker_startへ開始・分単位の見込みをINSERTする。
worker-dbがCLI起動記録とtask紐付けを同じtransactionで行う。worker_processesへ手動INSERTしない。
worker_inboxは観測・統合結果だけで、新規依頼ではない。notesは自分の進捗に使う。remote変更・deploy・共有ベンチは禁止。'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('worktree', type=Path)
    p.add_argument('--parent', default=os.environ.get('CODEX_THREAD_ID') or os.environ.get('SCAFFOLD_PARENT_SESSION_ID'))
    p.add_argument('--ensure', action='store_true', help='reuse an already running launcher')
    p.add_argument('--dispatch', action='store_true', help='return after CLI launch; caller must later use --check-started')
    p.add_argument('--check-started', action='store_true', help='verify existing CLI and task registration without launching')
    p.add_argument('--startup-timeout', type=float, default=120, help='seconds to wait for task registration')
    p.add_argument('--receipt', type=Path, help=argparse.SUPPRESS)
    p.add_argument('--run', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--check', action='store_true', help='validate only; do not open iTerm or start Codex')
    a = p.parse_args()
    wt = a.worktree.resolve()
    repo = root(Path(__file__).resolve().parent.parent)
    if root(wt) != repo or wt == repo:
        p.error('specify a separate worktree belonging to this repository')
    if not a.parent or not (wt / '.local/lane.md').is_file():
        p.error('parent session ID and .local/lane.md are required')
    branch = git(wt, 'branch', '--show-current')
    mode = git(wt, 'config', f'branch.{branch}.scaffold-mode')
    purpose = git(wt, 'config', f'branch.{branch}.description')
    if mode not in ('phase1', 'task') or not purpose or a.startup_timeout < 0:
        p.error('valid lane mode, purpose and startup timeout are required')
    context_path = wt / '.local/worker-context.json'
    if a.check_started:
        task = wait_started(repo, context_path, a.startup_timeout, wt, a.parent)
        print(f"Worker task registered: {task['task_id']} (session {task['session_id']})")
        return
    codex = shutil.which('codex')
    if not codex:
        p.error('codex not found')
    if a.check:
        print('worktree, lane, parent and Codex: OK (no worker started)')
        return
    key = hashlib.sha256(str(wt).encode()).hexdigest()[:16]
    if not a.run:
        with (local(repo) / ('worker-' + key + '.lock')).open('a+') as check:
            try:
                fcntl.flock(check, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if a.ensure:
                    if a.dispatch:
                        print('Worker CLI already running; task registration still requires --check-started.')
                    else:
                        task = wait_started(repo, context_path, a.startup_timeout, wt, a.parent)
                        print(f"Worker task registered: {task['task_id']}")
                    return
                p.error('worker already running')
        with connect(repo) as db:
            active = db.execute("SELECT task_id FROM workers WHERE worktree=? AND state IN ('working','blocked','developed')", (str(wt),)).fetchone()
        if active:
            p.error('unfinished task exists; resume its session explicitly')
        if sys.platform != 'darwin':
            p.error('iTerm launch requires macOS')
        receipt = local(repo) / ('worker-launch-' + uuid.uuid4().hex + '.json')
        command = shlex.join([sys.executable, str(Path(__file__).resolve()), str(wt), '--parent', a.parent, '--run', '--receipt', str(receipt)])
        subprocess.run(['osascript', '-e', APPLE_SCRIPT, command], check=True, timeout=20)
        deadline = time.monotonic() + 20
        while not receipt.exists() and time.monotonic() < deadline:
            time.sleep(.1)
        if not receipt.exists():
            p.error('iTerm opened but CLI startup was not confirmed; inspect its tab')
        record = json.loads(receipt.read_text())
        if record.get('state') != 'running':
            p.error('worker exited during startup; inspect its tab')
        if a.dispatch:
            print('Interactive worker CLI launched in visible iTerm; task registration is pending (--check-started required).')
        else:
            task = wait_started(repo, context_path, a.startup_timeout)
            print(f"Interactive worker ready: task {task['task_id']} (session {task['session_id']})")
        return
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        p.error('worker must run in an interactive terminal')
    key = hashlib.sha256(str(wt).encode()).hexdigest()[:16]
    with (local(repo) / ('worker-' + key + '.lock')).open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            p.error('a worker launcher is already running for this worktree')
        with connect(repo) as db:
            active = db.execute("SELECT task_id FROM workers WHERE worktree=? AND state IN ('working','blocked','developed')", (str(wt),)).fetchone()
        if active:
            p.error('existing unfinished task: resume that session instead of starting another worker')
        process_id = uuid.uuid4().hex
        receipt = a.receipt or local(repo) / ('worker-launch-' + process_id + '.json')
        prompt = worker_prompt(context_path, a.parent, mode, purpose)
        env = os.environ.copy()
        for name in ('CODEX_THREAD_ID', 'SCAFFOLD_SESSION_ID', 'SCAFFOLD_TASK_ID', 'ISUSCOPE_LOCK_HELD', 'SCAFFOLD_BASE_COMMIT'):
            env.pop(name, None)
        env.update(SCAFFOLD_ROLE='worker', SCAFFOLD_AGENT='codex', SCAFFOLD_PARENT_SESSION_ID=a.parent)
        # The child inherits the terminal; no pipes, detached process or `codex exec`.
        child = subprocess.Popen([codex, '-C', str(wt), '-m', 'gpt-6-astra', '-c', 'model_reasoning_effort="medium"', '-s', 'danger-full-access', '-a', 'never', prompt], env=env)
        record = {'state': 'running', 'process_id': process_id, 'pid': child.pid, 'worktree': str(wt),
                  'parent_session_id': a.parent, 'branch': branch, 'mode': mode, 'purpose': purpose}
        save_json(context_path, record)
        save_json(receipt, record)
        def stop(signum, _):
            if child.poll() is None:
                child.send_signal(signum)
        signal.signal(signal.SIGHUP, stop)
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, lambda *_: None)  # terminal also delivers Ctrl-C to Codex
        code = child.wait()
        with connect(repo) as db:
            db.execute("UPDATE worker_processes SET exited_at=strftime('%s','now'),exit_code=? WHERE process_id=?", (code, process_id))
        record.update(state='exited', exit_code=code)
        save_json(context_path, record)
        save_json(receipt, record)
        print(f'Worker exited ({code}); worktree retained.')
        raise SystemExit(code)


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        sys.exit(str(exc))
