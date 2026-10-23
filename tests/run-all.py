#!/usr/bin/env python3
"""Run every tests/test_*.py and tests/*.test.js in its own process, several at a time (make test, CI).

Each test file builds its own temporary repositories and processes, so the files run side by side
and the suite takes about as long as its slowest file instead of the sum. A file's output is printed
whole when it finishes, and only for a file that failed; the last line counts the tests.

  python3 tests/run-all.py              as many files at once as there are CPUs
  TEST_JOBS=1 python3 tests/run-all.py  one at a time
  python3 tests/run-all.py test_board   only the named files

The JavaScript files (the board's) run with `node --test`; without node they are skipped and said so.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

TESTS = Path(__file__).resolve().parent


def run(path):
    started = time.monotonic()
    if path.name.endswith('.test.js'):
        command, count = ['node', '--test', str(path)], r'^ℹ tests (\d+)'
    else:
        command = [sys.executable, '-m', 'unittest', 'discover', '-s', str(TESTS), '-p', path.name, '-v']
        count = r'^Ran (\d+) tests?'
    done = subprocess.run(command, cwd=TESTS.parent, capture_output=True, text=True)
    output = done.stdout + done.stderr
    ran = re.findall(count, output, re.M)
    return path.name, done.returncode, output, int(ran[-1]) if ran else 0, time.monotonic() - started


def main():
    names = {name.removesuffix('.py') for name in sys.argv[1:]}
    scripts = sorted(TESTS.glob('*.test.js'))
    if scripts and not shutil.which('node'):
        print(f'skipped {len(scripts)} JavaScript test files: node is not installed', flush=True)
        scripts = []
    files = [path for path in sorted(TESTS.glob('test_*.py')) + scripts
             if not names or path.stem in names or path.name in names]
    # Larger files hold the longer flows; starting them first keeps the last file from running alone.
    files.sort(key=lambda path: path.stat().st_size, reverse=True)
    jobs = int(os.environ.get('TEST_JOBS') or os.cpu_count() or 4)
    started = time.monotonic()
    failed, total = [], 0
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for future in as_completed([pool.submit(run, path) for path in files]):
            name, code, output, count, seconds = future.result()
            total += count
            print(f'{"ok  " if code == 0 else "FAIL"} {name} ({count} tests, {seconds:.1f}s)', flush=True)
            if code:
                failed.append((name, output))
    for name, output in failed:
        print(f'\n===== {name} =====\n{output}', end='')
    print(f'\nRan {total} tests in {len(files)} files in {time.monotonic() - started:.1f}s with {jobs} at a time: '
          f'{"FAILED " + ", ".join(name for name, _ in failed) if failed else "OK"}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
