#!/usr/bin/env python3
"""Independent initial build lane: inspect one node, then build with reviewed settings.

When config/initial-build.json is missing, the lane proposes settings from the probe and the
imported Cargo files. A proposal it can justify (a known builder base whose glibc, and libssl
when the app links OpenSSL, fit the node) is written as the settings and the build starts at
once; the operator reviews and commits that file like a hand-written one. Anything it cannot
decide (a workspace, system libraries, no fitting base) waits for the operator as before.
INITIAL_BUILD_AUTO_CONFIG=0 always waits.
"""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import tomllib

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.local/initial-build'
CONFIG = ROOT / 'config/initial-build.json'
BASES = ROOT / 'config/rust-builder/bases.json'
TARGETS = {'x86_64': 'x86_64-unknown-linux-gnu', 'aarch64': 'aarch64-unknown-linux-gnu'}
# -sys crates whose system library and apt package depend on the app; the operator decides.
SYSTEM_LIBRARY_CRATES = {'mysqlclient-sys', 'pq-sys', 'libsqlite3-sys', 'curl-sys', 'libgit2-sys', 'libssh2-sys'}

_progress_spec = importlib.util.spec_from_file_location('progress', Path(__file__).resolve().parent / 'progress.py')
progress = importlib.util.module_from_spec(_progress_spec)
_progress_spec.loader.exec_module(progress)
NOTIFY = {'needs_configuration': 'config/initial-build.json を作ると初期buildが始まる（.local/initial-build/proposal.json）',
          'configured': '設定を自動生成した。config/initial-build.json を確認してcommitする',
          'building': '', 'complete': 'deployできる成果物がある', 'failed': '', 'stopped': ''}


_entered = None


def record_transition(state, now):
    """Append the time spent in the previous state, in the stage-lib.sh format."""
    global _entered
    if _entered:
        previous, since = _entered
        code = 1 if state == 'failed' else 0
        rows = [(previous, since, now - since, code)]
        if state in ('complete', 'failed'):
            # A zero-length row marks when the lane finished.
            rows.append((state, now, 0.0, code))
        try:
            timing = Path(os.environ.get('STAGE_TIMING_FILE', ROOT / '.local/stage-timing.tsv'))
            timing.parent.mkdir(parents=True, exist_ok=True)
            with timing.open('a') as out:
                for name, at, seconds, status in rows:
                    stamp = time.strftime('%Y-%m-%dT%H:%M:%S%z', time.localtime(at))
                    out.write(f'{stamp}\tinitial-build\t{name}\t{seconds:.1f}\t{status}\n')
        except OSError:
            pass
    _entered = (state, now)


def save(state, **fields):
    STATE.mkdir(parents=True, exist_ok=True)
    record_transition(state, time.time())
    p = STATE / 'status.json'
    temp = p.with_suffix('.tmp')
    temp.write_text(json.dumps(dict(state=state, updated_at=time.time(), **fields), indent=2) + '\n')
    temp.replace(p)
    if state in NOTIFY:
        progress.emit('initial-build', state, fields.get('error') or NOTIFY[state], path=ROOT / '.local/progress.log')


def version(text):
    return tuple(int(part) for part in text.split('.'))


def propose(environment, application):
    """Settings for config/initial-build.json, or None with the reasons the operator must decide."""
    lines = environment.splitlines()
    reasons = []
    target = TARGETS.get(lines[0].strip() if lines else '')
    if not target:
        reasons.append('unsupported or unknown CPU architecture')
    glibc = next((m.group(1) for line in lines if (m := re.match(r'^glibc (\d+\.\d+)', line.strip()))), None)
    if not glibc:
        reasons.append('glibc version not found in the probe')
    openssl = next((m.group(1) for line in lines if (m := re.match(r'^OpenSSL (\d+)\.(\d+)', line.strip()))), None)
    app = ROOT / application
    binary = None
    packages = set()
    try:
        manifest = tomllib.loads((app / 'Cargo.toml').read_text())
        lock = tomllib.loads((app / 'Cargo.lock').read_text())
        packages = {p.get('name') for p in lock.get('package', [])}
        bins = [b.get('name') for b in manifest.get('bin', [])]
        if 'package' not in manifest:
            reasons.append('Cargo.toml is a workspace; set source_root and manifest_dir')
        elif len(bins) > 1:
            reasons.append(f'several binaries: {", ".join(map(str, bins))}')
        else:
            binary = bins[0] if bins else manifest['package'].get('name')
            if not isinstance(binary, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', binary):
                reasons.append(f'unusable binary name: {binary!r}')
                binary = None
    except FileNotFoundError as missing:
        reasons.append(f'{Path(missing.filename).name} not found in {application}; the workspace root may be elsewhere')
    except (OSError, tomllib.TOMLDecodeError) as error:
        reasons.append(f'cannot read Cargo files: {error}')
    linked = sorted(packages & SYSTEM_LIBRARY_CRATES)
    if linked:
        reasons.append(f'links system libraries ({", ".join(linked)}); choose base and packages')
    needs_ssl = 'openssl-sys' in packages
    libssl = None if not openssl else ('1.1' if version(openssl) < (3,) else '3')
    if needs_ssl and not libssl:
        reasons.append('the app links OpenSSL but the node has no openssl command to compare libssl')
    try:
        bases = json.loads(BASES.read_text())['bases']
    except (OSError, ValueError, KeyError):
        bases = []
        reasons.append('config/rust-builder/bases.json is missing')
    base = None
    if glibc:
        fitting = [b for b in bases if version(b['glibc']) <= version(glibc) and (not needs_ssl or b.get('libssl') == libssl)]
        base = max(fitting, key=lambda b: version(b['glibc']), default=None)
        if not base:
            reasons.append(f'no builder base with glibc <= {glibc}' + (f' and libssl {libssl}' if needs_ssl else ''))
    if reasons:
        return None, reasons
    return {'base_image': base['image'], 'target': target, 'binary': binary,
            'dockerfile': 'config/rust-builder/Dockerfile'}, [
        f'node glibc {glibc}' + (f', libssl {libssl} (openssl-sys)' if needs_ssl else '') + f'; base glibc {base["glibc"]}']


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
    print(f'initial build lane dispatched (pid {child.pid}); status: {STATE / "status.json"}', flush=True)


def run(node, application):
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / 'lane.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Build lane already running', flush=True)
            return
        global _entered
        _entered = None
        save('inspecting', pid=os.getpid(), node=node)
        try:
            # Read-only and independent of bootstrap/operation.lock; no application execution.
            probe = "uname -m; cat /etc/os-release; getconf GNU_LIBC_VERSION; openssl version 2>/dev/null; sudo -n -iu isucon sh -c 'command -v rustc; rustc --version; cargo --version' || true"
            result = subprocess.run([str(ROOT / 'scripts/ssh-node.sh'), node, probe],
                                    text=True, capture_output=True, timeout=40)
            (STATE / 'environment.txt').write_text(result.stdout + result.stderr)
            if result.returncode:
                raise RuntimeError('build environment probe failed; see environment.txt')
            if not CONFIG.exists():
                settings, reasons = propose(result.stdout, application)
                (STATE / 'proposal.json').write_text(json.dumps({'settings': settings, 'reasons': reasons}, indent=2) + '\n')
                if settings and os.environ.get('INITIAL_BUILD_AUTO_CONFIG', '1') != '0':
                    temp = CONFIG.with_name(CONFIG.name + '.tmp')
                    temp.write_text(json.dumps(settings, indent=2) + '\n')
                    temp.replace(CONFIG)
                    save('configured', pid=os.getpid(), node=node, config=settings, reasons=reasons)
            if not CONFIG.exists():
                save('needs_configuration', pid=os.getpid(), evidence=str(STATE / 'environment.txt'),
                     next='Review evidence and Cargo manifests, create config/initial-build.json, the waiting build lane starts automatically')
                print('BUILD NEEDS CONFIGURATION: config/initial-build.json; see .local/initial-build/environment.txt', flush=True)
                deadline = time.monotonic() + 1800
                while not CONFIG.exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError('build settings were not supplied within 30 minutes')
                    time.sleep(1)
            config = json.loads(CONFIG.read_text())
            if not {'base_image', 'target', 'binary', 'dockerfile'} <= set(config) or set(config) - {'base_image', 'target', 'binary', 'dockerfile', 'source_root', 'manifest_dir', 'toolchain', 'packages'}:
                raise ValueError('initial-build.json needs base_image, target, binary, dockerfile')
            expected = TARGETS.get(result.stdout.splitlines()[0])
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
