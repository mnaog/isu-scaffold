"""The hooks as Claude Code runs them: scripts/hook.sh with isu-hook built from tools/isu-hook."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / 'scripts/hook.sh'
BINARY = ROOT / '.local/isu-hook/release/isu-hook'
_built = False


def build():
    """Builds isu-hook once per test process (make hook; cargo skips it when nothing changed)."""
    global _built
    if not _built:
        subprocess.run(['make', '-s', '-C', str(ROOT), 'hook'], check=True)
        _built = True
    return BINARY


def command(mode):
    build()
    return [str(HOOK), mode]
