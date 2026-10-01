"""One fresh CLI invocation per scout; scheduling belongs to scaffold."""
import concurrent.futures
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
from inputs import generate, prompt
from store import config, connect, local, lock, rows


def initialize(repo, cfg):
    with connect(repo) as db:
        for name, spec in cfg['scouts'].items():
            db.execute('INSERT OR IGNORE INTO scouts(name) VALUES (?)', (name,))
            if spec.get('unavailable'):
                db.execute("UPDATE scouts SET error=? WHERE name=? AND state='stopped'", (spec['unavailable'], name))


def export_board(repo):
    with lock(repo, 'export', blocking=True):
        with connect(repo) as db:
            reports = rows(db, 'SELECT * FROM scouts ORDER BY name')
        parts = ['# Scout board\n\n各scoutの最新報告。自動生成・通常のコミットに含める。\n']
        for r in reports:
            parts.append(f"\n## {r['name']}\n\n")
            if r['report'] is None:
                parts.append('未投稿\n')
                continue
            stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(r['posted_at']))
            parts.append(f"投稿: {stamp} / commit: `{r['code_commit']}` / run: `{r['run_id'] or 'なし'}` / base: `{r['base_run_id'] or 'なし'}`\n\n")
            # Quote model output as plain text, not executable HTML/Markdown.
            import html
            parts.extend('> ' + html.escape(line) + '\n' for line in r['report'].splitlines())
        from research import export_research
        parts.append(export_research(repo))
        target = repo / 'docs' / 'scout-board.md'
        tmp = target.with_suffix('.md.tmp')
        tmp.write_text(''.join(parts))
        tmp.replace(target)


def save_report(repo, name, report, bundle, ref, session, now=None):
    report = report.strip()
    if not 1 <= len(report) <= 300:
        raise ValueError(f'報告は1〜300字が必要です（{len(report)}字）。切り捨てず失敗として扱います')
    m = bundle['metrics']
    with connect(repo) as db:
        db.execute('''UPDATE scouts SET report=?,posted_at=?,input_ref=?,code_commit=?,run_id=?,base_run_id=?,session_id=?,failures=0,error=NULL WHERE name=?''',
                   (report, time.time() if now is None else now, str(ref), m['current_commit'],
                    (m.get('latest') or {}).get('id'), (m.get('base') or {}).get('id'), session, name))
    export_board(repo)


def terminate(p):
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    p.wait()


def invoke(repo, spec, text, directory, stop, timeout, session, lock_fd=None, role="scout", extra_env=None):
    output = directory / 'report.txt'
    argv = [v.replace('{model}', spec['model']).replace('{output}', str(output)).replace('{session}', session) for v in spec['argv']]
    if shutil.which(argv[0]) is None:
        raise FileNotFoundError(f'No such file: CLI {argv[0]}')
    env = os.environ.copy()
    # A fresh invocation must not inherit the operator's identity or nesting guard.
    for key in ('CODEX_THREAD_ID', 'CLAUDECODE', 'SCAFFOLD_SESSION_ID', 'SCAFFOLD_PARENT_SESSION_ID', 'SCAFFOLD_TASK_ID',
                'SCAFFOLD_RESEARCH_JOB_ID', 'SCAFFOLD_INVOCATION_ID'):
        env.pop(key, None)
    env['SCAFFOLD_ROLE'] = role
    env.pop('SCAFFOLD_AGENT', None)
    env.update(extra_env or {})
    (directory / 'prompt.txt').write_text(text)
    with (directory / 'prompt.txt').open() as stdin, (directory / 'stdout.log').open('w') as stdout, (directory / 'stderr.log').open('w') as stderr:
        guarded = [sys.executable, str(Path(__file__).with_name('process_guard.py')), str(os.getpid()), str(timeout + 5), *argv]
        p = subprocess.Popen(guarded, cwd=repo, stdin=stdin, stdout=stdout, stderr=stderr, env=env, start_new_session=True, pass_fds=(() if lock_fd is None else (lock_fd,)))
        (directory / 'process.json').write_text(json.dumps({'pid': p.pid, 'invocation_id': session, 'argv': argv, 'started_at': time.time()}))
        deadline = time.monotonic() + timeout
        try:
            while p.poll() is None:
                if stop.wait(0.1):
                    raise InterruptedError('停止されました')
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'CLI timeout ({timeout}s)')
            if p.returncode:
                raise RuntimeError(f'CLI exit {p.returncode}。詳細: {directory.relative_to(repo)}')
        finally:
            # Also terminate descendants if the parent exited first.
            terminate(p)
            (directory / 'exit.json').write_text(json.dumps({'exit_code': p.returncode, 'exited_at': time.time()}))
    raw = (directory / 'stdout.log').read_text()
    if spec['output'] == 'opencode-json':
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        if any(e.get('type') == 'error' for e in events):
            raise RuntimeError('OpenCode returned error; see local stdout.log')
        finished = [e for e in events if e.get('type') == 'step_finish']
        if not finished or finished[-1].get('part', {}).get('reason') != 'stop':
            raise ValueError('OpenCodeの正常な最終回答がありません（step_finish/stop未確認）')
        final = finished[-1]
        session_id = final.get('sessionID')
        message_id = final.get('part', {}).get('messageID')
        if not session_id or not message_id:
            raise ValueError('OpenCode sessionID/messageIDがありません')
        # Intermediate exploration messages and tool output must not become the report.
        texts = {}
        for event in events:
            part = event.get('part', {})
            if (event.get('type') == 'text' and event.get('sessionID') == session_id
                    and part.get('messageID') == message_id and part.get('id')):
                texts[part['id']] = part.get('text', '')
        if not texts:
            raise ValueError('OpenCode最終回答のtextがありません')
        (directory / 'model.json').write_text(json.dumps({'requested_model': spec['model'], 'session_id': session_id}))
        return '\n'.join(texts.values())
    if spec['output'] == 'codex-json':
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        thread = next((e.get('thread_id') for e in events if e.get('type') == 'thread.started'), None)
        if not thread:
            raise ValueError('Codex thread.startedがありません')
        (directory / 'model.json').write_text(json.dumps({'models': [spec['model']], 'session_id': thread}))
        return output.read_text()
    if spec['output'] == 'file':
        return output.read_text()
    if spec['output'] == 'claude-json':
        value = json.loads(raw)
        if value.get('is_error'):
            raise RuntimeError('Claude Code returned is_error; see local stdout.log')
        (directory / 'model.json').write_text(json.dumps({'models': list(value.get('modelUsage', {})), 'session_id': value.get('session_id')}))
        return value['result']
    if spec['output'] != 'text':
        raise ValueError('unknown output format')
    return raw


def attempt(repo, cfg, name, stop=None, bundle=None, clock=time.time):
    stop = stop or threading.Event()
    with lock(repo, 'scout-' + name) as held:
        spec = cfg['scouts'][name]
        with connect(repo) as db:
            db.execute("UPDATE scouts SET state='running',started_at=?,next_at=NULL,error=NULL WHERE name=?", (clock(), name))
        try:
            if spec.get('unavailable') or not spec.get('model'):
                raise ValueError(spec.get('unavailable') or '正確なモデルIDが未設定')
            session = str(uuid.uuid4())
            directory = local(repo) / 'runs' / name / session
            directory.mkdir(parents=True)
            bundle = bundle if bundle is not None else generate(repo, cfg, stop)
            (directory / 'input.json').write_text(json.dumps(bundle, ensure_ascii=False, indent=2))
            if stop.is_set():
                raise InterruptedError('停止されました')
            report = invoke(repo, spec, prompt(repo, bundle), directory, stop, cfg['timeout_seconds'], session, lock_fd=held.fileno())
            if stop.is_set():
                raise InterruptedError('停止されました')
            metadata = directory / 'model.json'
            if metadata.exists():
                session = json.loads(metadata.read_text()).get('session_id') or session
            save_report(repo, name, report, bundle, directory.relative_to(repo), session, now=clock())
            # Count 15 minutes from completed publication (including Markdown export).
            with connect(repo) as db:
                db.execute("UPDATE scouts SET state='waiting',next_at=? WHERE name=?", (clock() + cfg['interval_seconds'], name))
        except Exception as exc:
            with connect(repo) as db:
                failures = db.execute('SELECT failures FROM scouts WHERE name=?', (name,)).fetchone()[0] + 1
                delay = min(cfg['retry_max_seconds'], cfg['interval_seconds'] * 2 ** min(failures - 1, 10))
                db.execute('UPDATE scouts SET state=?,error=?,failures=?,next_at=? WHERE name=?',
                           ('stopped' if stop.is_set() else 'error', str(exc)[:1000], failures,
                            None if stop.is_set() else clock() + delay, name))
            return False
        return True


def daemon(repo):
    with lock(repo, 'daemon'):
        cfg = config(repo)
        initialize(repo, cfg)
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        stopfile = local(repo) / 'stop-request'
        with connect(repo) as db:
            # Preserve due times across restarts. Interrupted work gets a cooldown.
            db.execute("UPDATE scouts SET state='error',error='前回の実行が中断されました',next_at=? WHERE state='running'", (time.time() + cfg['interval_seconds'],))
            db.execute("UPDATE scouts SET state='waiting' WHERE state='stopped' AND next_at IS NOT NULL")
        futures = {}
        deferred = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(cfg['scouts'])) as pool:
            while not stop.is_set():
                if stopfile.exists():
                    stop.set()
                    break
                now = time.time()
                with connect(repo) as db:
                    states = rows(db, 'SELECT name,next_at FROM scouts')
                for r in states:
                    name = r['name']
                    if name not in cfg['scouts'] or (name in futures and not futures[name].done()):
                        continue
                    if name in futures:
                        try:
                            futures.pop(name).result()
                        except RuntimeError:
                            deferred[name] = now + cfg['interval_seconds']
                    if deferred.get(name, 0) > now:
                        continue
                    if r['next_at'] is None or r['next_at'] <= now:
                        futures[name] = pool.submit(attempt, repo, cfg, name, stop)
                stop.wait(0.25)
        with connect(repo) as db:
            db.execute("UPDATE scouts SET state='stopped'")


def active(repo):
    try:
        with lock(repo, 'daemon'):
            return False
    except RuntimeError:
        return True


def start(repo):
    with lock(repo, 'control', blocking=True):
        if active(repo):
            raise RuntimeError('scout daemon: already running')
        (local(repo) / 'stop-request').unlink(missing_ok=True)
        with (local(repo) / 'daemon.log').open('a') as log:
            import sys
            p = subprocess.Popen([sys.executable, str(Path(__file__).with_name('main.py')), 'daemon'], cwd=repo,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        threading.Thread(target=p.wait, daemon=True).start()
        for _ in range(50):
            if active(repo):
                return p.pid
            if p.poll() is not None:
                raise RuntimeError('daemon起動失敗。 .local/operations/daemon.log を確認してください')
            time.sleep(0.1)
        raise RuntimeError('daemon起動確認がtimeoutしました')


def stop_daemon(repo):
    with lock(repo, 'control', blocking=True):
        (local(repo) / 'stop-request').touch()
        for _ in range(450):
            if not active(repo):
                with connect(repo) as db:
                    db.execute("UPDATE scouts SET state='stopped'")
                return
            time.sleep(0.1)
        raise RuntimeError('停止要求済み。入力取得の終了待ちです。statusで確認してください')
