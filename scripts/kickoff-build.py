#!/usr/bin/env python3
"""Independent kickoff build lane: inspect one node, then build with reviewed settings.

When config/kickoff-build.json is missing, the lane proposes settings from the probe and the
imported Go files. A proposal it can justify (the node's CPU, one Go unit whose ExecStart runs a
binary inside the application directory, one main package, no cgo-only module) is written as the
settings and the build starts at once; the operator reviews and commits that file like a
hand-written one. Anything it cannot decide waits for the operator as before.
KICKOFF_BUILD_AUTO_CONFIG=0 always waits.
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

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.local/kickoff-build'
CONFIG = ROOT / 'config/kickoff-build.json'
TARGETS = {'x86_64': 'linux/amd64', 'aarch64': 'linux/arm64'}
# Modules that only build with cgo; CGO_ENABLED=0 cannot build them, so the operator decides.
CGO_MODULES = {'github.com/mattn/go-sqlite3', 'github.com/mattn/go-oci8', 'gopkg.in/gographics/imagick.v2',
               'gopkg.in/gographics/imagick.v3', 'github.com/confluentinc/confluent-kafka-go'}
SETTINGS = {'target', 'binary', 'output', 'package', 'source_root', 'manifest_dir'}
# Reference units are named per language (isuride-go.service, isucondition.go.service).
GO_UNIT = re.compile(r'^.+[-._](go|golang)\.service$')

_progress_spec = importlib.util.spec_from_file_location('progress', Path(__file__).resolve().parent / 'progress.py')
progress = importlib.util.module_from_spec(_progress_spec)
_progress_spec.loader.exec_module(progress)
NOTIFY = {'needs_configuration': 'config/kickoff-build.json を作るとkickoff buildが始まる（.local/kickoff-build/proposal.json）',
          'configured': '設定を自動生成した。config/kickoff-build.json をcommitする',
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
                    out.write(f'{stamp}\tkickoff-build\t{name}\t{seconds:.1f}\t{status}\n')
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
        progress.emit('kickoff-build', state, fields.get('error') or NOTIFY[state], path=ROOT / '.local/progress.log')


def required_modules(go_mod):
    """Module paths in a go.mod's require directives (both the one-line and the block form)."""
    modules, block = set(), False
    for line in go_mod.splitlines():
        line = line.split('//', 1)[0].strip()
        if block:
            if line == ')':
                block = False
            elif line:
                modules.add(line.split()[0])
        elif line == 'require (':
            block = True
        elif line.startswith('require '):
            modules.add(line.split()[1])
    return modules


def main_packages(app):
    """Directories (relative, as Go package paths) whose .go files declare package main."""
    found = set()
    for path in app.rglob('*.go'):
        relative = path.relative_to(app)
        if path.name.endswith('_test.go') or any(part in ('vendor', 'testdata') or part.startswith('.') for part in relative.parts):
            continue
        try:
            head = path.read_text(errors='ignore')
        except OSError:
            continue
        if re.search(r'^package main\s*$', head, re.M):
            parent = relative.parent.as_posix()
            found.add('.' if parent == '.' else './' + parent)
    return sorted(found)


def propose(environment, application):
    """Settings for config/kickoff-build.json, or None with the reasons the operator must decide."""
    lines = environment.splitlines()
    reasons = []
    target = TARGETS.get(lines[0].strip() if lines else '')
    if not target:
        reasons.append('unsupported or unknown CPU architecture')
    app = ROOT / application
    package = None
    try:
        cgo = sorted(required_modules((app / 'go.mod').read_text()) & CGO_MODULES)
        if cgo:
            reasons.append(f'needs cgo ({", ".join(cgo)}); CGO_ENABLED=0 cannot build it')
        packages = main_packages(app)
        if len(packages) == 1:
            package = packages[0]
        else:
            reasons.append(f'main packages: {", ".join(packages) or "none"}; set package')
    except FileNotFoundError:
        reasons.append(f'go.mod not found in {application}; the module root may be elsewhere (set source_root and manifest_dir)')
    except OSError as error:
        reasons.append(f'cannot read Go files: {error}')
    # "unit<TAB>name<TAB>ExecStart" lines from the probe; the binary is the command the Go unit runs.
    units = [line.split('\t')[1:] for line in lines if line.startswith('unit\t') and len(line.split('\t')) == 3]
    commands = [command for name, command in units if GO_UNIT.match(name)]
    output = None
    if len(commands) != 1:
        reasons.append(f'Go units found: {", ".join(name for name, _ in units if GO_UNIT.match(name)) or "none"}; set binary and output')
    else:
        executable = commands[0].lstrip('-@:+!').split()[0] if commands[0].strip() else ''
        marker = f'/{application}/'
        if marker not in executable:
            reasons.append(f'the Go unit runs {executable or "nothing"}, outside {application}; set binary and output')
        else:
            output = executable.split(marker, 1)[1]
            if not re.fullmatch(r'[A-Za-z0-9_./-]+', output) or '..' in output.split('/'):
                reasons.append(f'unusable output path: {output!r}')
                output = None
    if reasons:
        return None, reasons
    binary = Path(output).name
    return {'target': target, 'binary': binary, 'output': output, 'package': package}, [
        f'the Go unit runs {commands[0].split()[0]}; main package {package}']


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
    print(f'kickoff build lane dispatched (pid {child.pid}); status: {STATE / "status.json"}', flush=True)


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
            probe = ("uname -m; cat /etc/os-release; "
                     "for f in /etc/systemd/system/*.service; do test -f \"$f\" || continue; "
                     "printf 'unit\\t%s\\t%s\\n' \"${f##*/}\" \"$(sed -n 's/^ExecStart=//p' \"$f\" | head -n 1)\"; done; "
                     "sudo -n -iu isucon sh -c 'command -v go; go version' || true")
            result = subprocess.run([str(ROOT / 'scripts/ssh-node.sh'), node, probe],
                                    text=True, capture_output=True, timeout=40)
            (STATE / 'environment.txt').write_text(result.stdout + result.stderr)
            if result.returncode:
                raise RuntimeError('build environment probe failed; see environment.txt')
            if not CONFIG.exists():
                settings, reasons = propose(result.stdout, application)
                (STATE / 'proposal.json').write_text(json.dumps({'settings': settings, 'reasons': reasons}, indent=2) + '\n')
                if settings and os.environ.get('KICKOFF_BUILD_AUTO_CONFIG', '1') != '0':
                    temp = CONFIG.with_name(CONFIG.name + '.tmp')
                    temp.write_text(json.dumps(settings, indent=2) + '\n')
                    temp.replace(CONFIG)
                    save('configured', pid=os.getpid(), node=node, config=settings, reasons=reasons)
            if not CONFIG.exists():
                save('needs_configuration', pid=os.getpid(), evidence=str(STATE / 'environment.txt'),
                     next='Review evidence and the Go module, create config/kickoff-build.json, the waiting build lane starts automatically')
                print('BUILD NEEDS CONFIGURATION: config/kickoff-build.json; see .local/kickoff-build/environment.txt', flush=True)
                deadline = time.monotonic() + 1800
                while not CONFIG.exists():
                    if time.monotonic() >= deadline:
                        raise TimeoutError('build settings were not supplied within 30 minutes')
                    time.sleep(1)
            config = json.loads(CONFIG.read_text())
            if not {'target', 'binary'} <= set(config) or set(config) - SETTINGS:
                raise ValueError('kickoff-build.json needs target and binary')
            expected = TARGETS.get(result.stdout.splitlines()[0])
            if config['target'] != expected:
                raise ValueError('configured target does not match the source node architecture')
            manifest = {'items': [{'name': 'go-app', 'type': 'directory', 'local': application}],
                        'local_builds': [dict(config, item='go-app')]}
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
    p.add_argument('--application', default='webapp/go')
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
