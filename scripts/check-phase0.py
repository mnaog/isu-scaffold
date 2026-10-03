#!/usr/bin/env python3
"""Reject known post-distribution inputs in a Phase 0 export (not a semantic audit)."""
import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
AMI = re.compile(rb"ami-" + rb"[0-9a-f]{8,17}\b")
FORBIDDEN = {"config/benchmark-contract.json","config/phase1-build.json", "config/local/compose.yaml", "infra/practice-stack.json",
             "config/practice-stack.example.env", "scripts/practice-stack.py", "scripts/practice-stack.sh"}


def problems(files):
    errors = []
    for name, data in files:
        if name in FORBIDDEN:
            errors.append(f"{name}: application-specific runtime or organizer distribution")
        if name.startswith(("webapp/", "isuscope-data/", "docs/agent-history/", ".local/")):
            if Path(name).name.lower() not in (".gitkeep", "readme.md") or name.startswith(".local/"):
                errors.append(f"{name}: application/data/history/local state is not a Phase 0 input")
        if AMI.search(data):
            errors.append(f"{name}: concrete AMI ID must be received in Phase 1")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", help="check exactly the Git tree to be exported")
    args = parser.parse_args()
    def git(*argv):
        return subprocess.check_output(["git", *argv], cwd=ROOT)
    if args.ref:
        commit = git("rev-parse", "--verify", args.ref + "^{commit}").decode().strip()
        names = git("ls-tree", "-r", "--name-only", "-z", commit).decode().split("\0")
        files = ((n, git("show", f"{commit}:{n}")) for n in names if n)
    else:
        names = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")
        files = ((n, (ROOT / n).read_bytes()) for n in names if n and (ROOT / n).is_file())
    errors = problems(files)
    if errors:
        sys.exit("Phase 0 boundary check failed:\n" + "\n".join(errors))
    print("Phase 0 boundary check passed (known inputs only; review the preparation scope too)")


if __name__ == "__main__":
    main()
