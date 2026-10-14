# Per-task wall time for tuning the initial work, without the ansible.posix collection.
# Each row is started_at, playbook, play, task, seconds; the seconds cover every host
# because a linear play waits for all of them before the next task.
from __future__ import annotations

DOCUMENTATION = '''
name: task_timing
type: aggregate
short_description: append per-task wall time to a TSV file
description:
  - Writes one row per task to the file named by ANSIBLE_TASK_TIMING_FILE; does nothing when unset.
'''

import os
import time

from ansible.plugins.callback import CallbackBase


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'aggregate'
    CALLBACK_NAME = 'task_timing'
    CALLBACK_NEEDS_ENABLED = True

    def __init__(self):
        super().__init__()
        self._path = os.environ.get('ANSIBLE_TASK_TIMING_FILE')
        self._playbook = ''
        self._play = ''
        self._current = None

    def _finish(self):
        if not self._current or not self._path:
            self._current = None
            return
        name, started = self._current
        self._current = None
        stamp = time.strftime('%Y-%m-%dT%H:%M:%S%z', time.localtime(started))
        row = [stamp, self._playbook, self._play, name, '%.1f' % (time.time() - started)]
        try:
            with open(self._path, 'a') as out:
                out.write('\t'.join(value.replace('\t', ' ') for value in row) + '\n')
        except OSError:
            pass

    def _start(self, task):
        self._finish()
        self._current = (task.get_name().strip(), time.time())

    def v2_playbook_on_start(self, playbook):
        self._playbook = os.path.basename(playbook._file_name)

    def v2_playbook_on_play_start(self, play):
        self._finish()
        self._play = play.get_name().strip()

    def v2_playbook_on_task_start(self, task, is_conditional):
        self._start(task)

    def v2_playbook_on_handler_task_start(self, task):
        self._start(task)

    def v2_playbook_on_stats(self, stats):
        self._finish()
