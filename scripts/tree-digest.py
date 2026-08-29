#!/usr/bin/env python3
import hashlib
import json
import os
import stat
import sys
from pathlib import Path


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def record(path: Path, relative: str) -> dict[str, object]:
    metadata = path.lstat()
    result: dict[str, object] = {
        "path": relative,
        "mode": stat.S_IMODE(metadata.st_mode),
    }
    if stat.S_ISREG(metadata.st_mode):
        result.update(type="file", size=metadata.st_size, sha256=file_hash(path))
    elif stat.S_ISDIR(metadata.st_mode):
        result["type"] = "directory"
    elif stat.S_ISLNK(metadata.st_mode):
        result.update(type="symlink", target=os.readlink(path))
    else:
        result["type"] = "other"
    return result


def tree_digest(root: Path) -> str:
    if not root.exists() and not root.is_symlink():
        raise FileNotFoundError(root)
    is_directory = root.is_dir() and not root.is_symlink()
    entries = [{"path": ".", "type": "directory"}] if is_directory else [record(root, ".")]
    if is_directory:
        for current, directories, files in os.walk(root, followlinks=False):
            directories.sort()
            files.sort()
            current_path = Path(current)
            for name in directories + files:
                path = current_path / name
                relative = path.relative_to(root).as_posix()
                entries.append(record(path, relative))
    canonical = "\n".join(
        json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for entry in entries
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} PATH", file=sys.stderr)
        return 2
    try:
        print(tree_digest(Path(sys.argv[1])))
    except (OSError, ValueError) as error:
        print(f"cannot digest {sys.argv[1]}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
