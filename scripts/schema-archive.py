#!/usr/bin/env python3
"""Stream a bounded, partial schema archive; diagnostics go to stderr."""
import os
import sys
import tarfile
from pathlib import Path

def main():
    root = Path(sys.argv[1])
    per_file = int(sys.argv[2])
    total_limit = int(sys.argv[3])
    if per_file <= 0 or total_limit <= 0:
        raise SystemExit("schema byte limits must be positive")
    used = 0
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
        if not root.is_dir():
            print("MISSING schema directory: " + str(root), file=sys.stderr)
            return
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in {".git", "node_modules", "target"})
            for name in sorted(files):
                path = Path(current, name)
                relative = path.relative_to(root)
                size = path.lstat().st_size
                if path.is_symlink() or path.suffix.lower() not in {".sql", ".sh", ".py", ".rb", ".pl"}:
                    reason = "data-or-link"
                elif size > per_file:
                    reason = "file-size"
                elif used + size > total_limit:
                    reason = "total-size"
                else:
                    archive.add(path, arcname=str(relative), recursive=False)
                    used += size
                    print("INCLUDED " + str(relative), file=sys.stderr)
                    continue
                print("DEFERRED " + reason + " " + str(relative), file=sys.stderr)
    print("schema bytes: " + str(used), file=sys.stderr)

if __name__ == "__main__":
    main()
