#!/usr/bin/env python3
"""Independent Phase 1 build lane: inspect one node, then build with reviewed settings."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.local/phase1-build'
CONFIG = ROOT / 'config/phase1-build.json'


def save(state, **fields):
    STATE.mkdir(parents=True, exist_ok=True)
    p = STATE / 'status.json'
    temp = p.with_suffix('.tmp')
    temp.write_text(json.dumps(dict(state=state, updated_at=time.time(), **fields), indent=2) + '\n')
    temp.replace(p)


def start(node, application):
    STATE.mkdir(parents=True, exist_ok=True)
    # Runner owns the lock, including the preflight probe and compiler.
    env = os.environ.copy()
    env.pop('ISUSCOPE_LOCK_HELD', None)
    with (STATE / 'build.log').open('a') as log:
        child = subprocess.Popen([sys.executable, __file__, 'run', '--node', node,
                                  '--application', application], cwd=ROOT,
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 start_new_session=True, env=env)
    print(f'Phase 1 build lane dispatched (pid {child.pid}); status: {STATE / "status.json"}', flush=True)


def run(node, application):
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'lane.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Build lane already running', flush=True)
            return
        save('inspecting', pid=os.getpid(), node=node)
        try:
            # Read-only and independent of bootstrap/operation.lock; no application execution.
            probe = "uname -m; cat /etc/os-release; getconf GNU_LIBC_VERSION; sudo -n -iu isucon sh -c 'command -v rustc; rustc --version; cargo --version' || true"
            result = subprocess.run([str(ROOT / 'scripts/ssh-node.sh'), node, probe],
                                    text=True, capture_output=True, timeout=40)
            (STATE / 'environment.txt').write_text(result.stdout + result.stderr)
            if result.returncode:
                raise RuntimeError('build environment probe failed; see environment.txt')
            if not CONFIG.exists():
                save('needs_configuration', pid=os.getpid(), evidence=str(STATE / 'environment.txt'),
                     next='Review evidence and Cargo manifests, create config/phase1-build.json, the waiting build lane starts automatically')
                print('BUILD NEEDS CONFIGURATION: config/phase1-build.json; see .local/phase1-build/environment.txt', flush=True)
                deadline = time.monotonic() + 1800
                while not CONFIG.exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError('build settings were not supplied within 30 minutes')
                    time.sleep(1)
            config = json.loads(CONFIG.read_text())
            if set(config) != {'base_image', 'target', 'binary', 'dockerfile'}:
                raise ValueError('phase1-build.json needs base_image, target, binary, dockerfile')
            expected = {'x86_64': 'x86_64-unknown-linux-gnu', 'aarch64': 'aarch64-unknown-linux-gnu'}.get(result.stdout.splitlines()[0])
            if config['target'] != expected:
                raise ValueError('configured target does not match the source node architecture')
            manifest = {'items': [{'name': 'rust-app', 'type': 'directory', 'local': application}],
                        'local_builds': [dict(config, item='rust-app')]}
            path = STATE / 'manifest.json'
            path.write_text(json.dumps(manifest, indent=2) + '\n')
            subprocess.run([sys.executable, str(ROOT / 'scripts/local-build.py'), 'validate', '--manifest', str(path)], check=True)
            save('building', pid=os.getpid(), node=node, config=config)
            subprocess.run([sys.executable, str(ROOT / 'scripts/local-build.py'), 'build', '--manifest', str(path)], check=True, cwd=ROOT)
            save('complete', config=config)
        except Exception as exc:
            save('failed', error=str(exc))
            raise


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['start', 'run', 'status', 'stop'])
    p.add_argument('--node')
    p.add_argument('--application', default='webapp/rust')
    a = p.parse_args()
    if a.action == 'status':
        path = STATE / 'status.json'
        print(path.read_text() if path.exists() else 'not started')
        if path.exists() and json.loads(path.read_text())['state'] in ('failed', 'stopped'):
            raise SystemExit(1)
        return
    if a.action == 'stop':
        STATE.mkdir(parents=True, exist_ok=True)
        # A stale status must never be enough to signal a reused PID.
        with (STATE / 'lane.lock').open('a+') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                print('build lane is not running')
                return
            except BlockingIOError:
                status = json.loads((STATE / 'status.json').read_text())
                pid = status['pid']
                command = subprocess.check_output(['ps', '-p', str(pid), '-o', 'command='], text=True)
                if str(Path(__file__).resolve()) not in command or os.getpgid(pid) != pid:
                    raise RuntimeError('build process identity mismatch; refusing to signal')
                os.killpg(pid, signal.SIGTERM)
                save('stopped')
                return
    node = a.node
    if not node:
        inv = json.loads((ROOT / '.local/ansible-inventory.json').read_text())
        node = sorted(inv['all']['children']['application']['hosts'])[0]
    (start if a.action == 'start' else run)(node, a.application)


if __name__ == '__main__':
    main()
