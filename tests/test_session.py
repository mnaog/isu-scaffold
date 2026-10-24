import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SessionTests(unittest.TestCase):
    """make session starts each role's named Claude Code in its own place: main-bench in the root,
    the others in the worktree <role>-<repo>."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.repo = base / 'repo'
        (self.repo / 'scripts').mkdir(parents=True)
        shutil.copy(ROOT / 'scripts/session.sh', self.repo / 'scripts/session.sh')
        (self.repo / 'Makefile').write_text('hook:\n\t@true\n')
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(self.repo)], check=True)
        bin_dir = base / 'bin'
        bin_dir.mkdir()
        self.calls = base / 'claude-calls'
        # Records where claude started and with what (main-bench's board has no script here and just exits).
        (bin_dir / 'claude').write_text(
            '#!/usr/bin/env python3\nimport json, os, sys\n'
            f'open({str(self.calls)!r}, "a").write(json.dumps([os.getcwd(), sys.argv[1:]]) + "\\n")\n')
        (bin_dir / 'claude').chmod(0o755)
        self.env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}')

    def tearDown(self):
        self.tmp.cleanup()

    def session(self, role):
        return subprocess.run([str(self.repo / 'scripts/session.sh'), role], cwd=self.repo, env=self.env,
                              capture_output=True, text=True)

    def started(self):
        return json.loads(self.calls.read_text().splitlines()[-1])

    def test_each_role_starts_named_in_its_place(self):
        settings = ['--settings', 'config/claude/session.json']
        self.assertEqual(self.session('main-bench').returncode, 0)
        self.assertEqual(self.started(), [str(self.repo), ['--name', 'main-bench', *settings, '/main']])
        for role, prompt in (('human1', []), ('improve', ['/improve'])):
            place = self.repo.parent / f'{role}-repo'
            place.mkdir()
            result = self.session(role)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.started(), [str(place), ['--name', role, *settings, *prompt]])

    def test_a_missing_worktree_or_an_unknown_role_is_refused(self):
        missing = self.session('human2')
        self.assertEqual(missing.returncode, 1)
        self.assertIn(str(self.repo.parent / 'human2-repo'), missing.stderr)
        for role in ('main', 'human', 'human-codex'):
            self.assertEqual(self.session(role).returncode, 2, role)
        self.assertFalse(self.calls.exists())


if __name__ == '__main__':
    unittest.main()
