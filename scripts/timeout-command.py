#!/usr/bin/env python3
import os
import signal
import subprocess
import sys


def main() -> int:
    if len(sys.argv) < 3:
        print(f"usage: {sys.argv[0]} SECONDS COMMAND [ARG ...]", file=sys.stderr)
        return 2
    seconds = int(sys.argv[1])
    process = subprocess.Popen(sys.argv[2:], start_new_session=True)
    try:
        return process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        print(f"benchmark command timed out after {seconds}s", file=sys.stderr)
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
