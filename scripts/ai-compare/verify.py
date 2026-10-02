#!/usr/bin/env python3
"""Run pinned native Rust validation and record wall-clock intervals."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def build_environment(overrides):
    # Build variables are fixed; do not inherit user-specific compiler overrides.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('CARGO_', 'RUST', 'DYLD_'))
           and k not in ('CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'LDFLAGS',
                         'AR', 'MACOSX_DEPLOYMENT_TARGET', 'SDKROOT')}
    env.update(overrides)
    return env


def validate(root, mode):
    if mode not in ('check', 'test', 'mysql', 'acceptance'):
        raise SystemExit('usage: python3 verify.py check|test|mysql|acceptance')
    cfg = json.loads((root / '.local/environment.json').read_text())
    env = build_environment(cfg['build_env'])
    env['CARGO_TARGET_DIR'] = str(root / '.local/target')
    if mode in ('mysql', 'acceptance'):
        env['ISUCON_CACHE_TEST_DATABASE_URL'] = cfg['database_url']
    # Shared physical CPU: record lock waiting separately from compilation.
    queued = time.time()
    with open(cfg['build_lock'], 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        started = time.time()
        tick = time.monotonic()
        command = [cfg['cargo'], '+' + cfg['toolchain'], ('test' if mode in ('mysql', 'acceptance') else mode), '--locked', '--offline']
        if mode in ('mysql', 'acceptance'):
            command += [('new_user_cache_mysql' if mode == 'mysql' else 'medium_acceptance'), '--', '--ignored', '--test-threads=1', '--nocapture']
        code = 130
        try:
            code = subprocess.run(command, cwd=root / 'webapp/rust', env=env).returncode
        finally:
            record = {'phase': 'build' if mode == 'check' else mode,
                      'command': command, 'queued_at': queued, 'started_at': started,
                      'ended_at': time.time(), 'elapsed_seconds': time.monotonic() - tick,
                      'queue_seconds': started - queued, 'exit_code': code,
                      'trial': cfg['trial']}
            with (root / '.local/validation.jsonl').open('a') as log:
                log.write(json.dumps(record) + '\n')
        return code


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: python3 verify.py check|test|mysql|acceptance')
    sys.exit(validate(Path(__file__).resolve().parent, sys.argv[1]))
