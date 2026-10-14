"""Keep the CLI tied to its scaffold parent, including parent SIGKILL/crash."""
import os
import signal
import subprocess
import sys
import time

parent = int(sys.argv[1])
timeout = float(sys.argv[2])
# This process is the process-group leader. CLI descendants inherit its group.
child = subprocess.Popen(sys.argv[3:])
stopped = False


def stop(*_):
    global stopped
    stopped = True


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
deadline = time.monotonic() + timeout
while child.poll() is None:
    if stopped or os.getppid() != parent or time.monotonic() >= deadline:
        break
    time.sleep(.1)
code = child.poll()
interrupted = code is None or os.getppid() != parent
# Always clean up descendants, even if the CLI leader exited first.
os.killpg(os.getpgrp(), signal.SIGTERM)
try:
    child.wait(timeout=2)
except subprocess.TimeoutExpired:
    os.killpg(os.getpgrp(), signal.SIGKILL)
if interrupted:
    os.killpg(os.getpgrp(), signal.SIGKILL)
# On normal exit the parent cleans up any remaining descendants.
sys.exit(code if code is not None else 124)
