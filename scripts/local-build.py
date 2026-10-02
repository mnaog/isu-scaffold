#!/usr/bin/env python3
"""Build committed Rust sources locally and attach verified artifacts to deploy archives."""
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

REPO = Path(__file__).resolve().parent.parent
TARGETS = {"x86_64-unknown-linux-gnu": (62, "x86_64"),
           "aarch64-unknown-linux-gnu": (183, "aarch64")}


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def capture(*args):
    return subprocess.check_output(args, cwd=REPO).decode().strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", value) or any(
            p in ("", ".", "..") for p in value.split("/")):
        raise ValueError(f"unsafe relative path: {value!r}")
    return value


def definitions(manifest):
    builds = manifest.get("local_builds", [])
    if not isinstance(builds, list):
        raise ValueError("local_builds must be an array")
    items = {item["name"]: item for item in manifest["items"]}
    seen = set()
    for build in builds:
        if set(build) != {"item", "dockerfile", "base_image", "target", "binary"}:
            raise ValueError("local_builds requires item, dockerfile, base_image, target, binary")
        item = items.get(build["item"])
        if not item or item["type"] != "directory" or build["item"] in seen:
            raise ValueError("local build requires a unique directory item")
        seen.add(build["item"])
        if any(b["item"] == build["item"] for b in manifest.get("build_commands", [])):
            raise ValueError("do not combine local and remote builds for the same item")
        safe_path(item["local"])
        if not safe_path(build["dockerfile"]).startswith("config/"):
            raise ValueError("builder Dockerfile must be under config/")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", build["binary"]):
            raise ValueError("invalid Rust binary name")
        if build["target"] not in TARGETS:
            raise ValueError("unsupported Rust target")
        if not isinstance(build["base_image"], str) or not re.fullmatch(r"[A-Za-z0-9_./:@-]+", build["base_image"]):
            raise ValueError("invalid builder base_image")
    return builds


def clean(path):
    capture("git", "cat-file", "-e", f"HEAD:{path}")
    if capture("git", "status", "--porcelain", "--untracked-files=all", "--", path):
        raise ValueError(f"commit {path} before building/deploying")


def elf_check(data, target):
    if data[:6] != b"\x7fELF\x02\x01" or len(data) < 20 or int.from_bytes(data[18:20], "little") != TARGETS[target][0]:
        raise ValueError(f"artifact is not a 64-bit Linux ELF for {target}")


def verified(record):
    data = Path(record["artifact"]).read_bytes()
    if digest(data) != record["sha256"]:
        raise ValueError(f"artifact checksum mismatch: {record['artifact']}")
    elf_check(data, record["target"])
    return data


def build_one(build, item, cache, strict):
    source = item["local"]
    clean(source)
    if strict:
        clean(build["dockerfile"])
        clean("scripts/local-build.py")
    tree = capture("git", "rev-parse", f"HEAD:{source}")
    capture("git", "cat-file", "-e", f"HEAD:{source}/Cargo.lock")
    recipe = (REPO / build["dockerfile"]).read_bytes()
    platform = capture("docker", "info", "--format", "{{.OSType}}/{{.Architecture}}")
    platform = platform.replace("/aarch64", "/arm64").replace("/x86_64", "/amd64")
    if platform not in ("linux/arm64", "linux/amd64"):
        raise ValueError(f"unsupported Docker host: {platform}")
    recipe_id = digest(recipe + json.dumps([build["base_image"], build["target"], platform]).encode())
    tag = f"isucon-rust-builder:{recipe_id}"
    if subprocess.run(["docker", "image", "inspect", tag], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode:
        run("docker", "build", "--platform", platform, "--build-arg", f"BASE_IMAGE={build['base_image']}",
            "--build-arg", f"TARGET={build['target']}", "-t", tag, "-", input=recipe,
            stdout=sys.stderr)
    image = capture("docker", "image", "inspect", "--format", "{{.Id}}", tag)
    # Tree/lock changes invalidate the final artifact, but retain dependency caches.
    environment = digest(json.dumps([image, build["target"], source, build["binary"]]).encode())
    input_id = digest(json.dumps([tree, build, environment,
                                 digest(Path(__file__).read_bytes())], sort_keys=True).encode())
    output = cache / input_id
    metadata = output / "artifact.json"
    if metadata.exists():
        record = json.loads(metadata.read_text())
        if record["input_id"] != input_id:
            raise ValueError("artifact input mismatch")
        verified(record)
        print(f"local build: cached {build['binary']} ({input_id[:12]})", file=sys.stderr)
        return record
    project = digest(str(cache).encode())[:12]
    volume = f"isucon-rust-{project}-{environment[:20]}"
    run("docker", "volume", "create", volume, stdout=subprocess.DEVNULL)
    container = f"isucon-build-{uuid.uuid4().hex[:16]}"
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    # /work stays constant for Cargo fingerprints; target/registry live on Linux volumes.
    script = '''set -eu
mkdir -p /work /cache/cargo
tar -C /work -xf -
export CARGO_HOME=/cache/cargo
cd "$1"
if test -d /vendor; then
  mkdir -p /cache/vendor-home
  export CARGO_HOME=/cache/vendor-home
  printf '[source.crates-io]\nreplace-with = "vendor"\n[source.vendor]\ndirectory = "/vendor"\n' > "$CARGO_HOME/config.toml"
fi
cargo build --locked --release --target "$2" --bin "$3"
'''
    command = ["docker", "run", "--name", container, "-i", "--platform", platform,
               "--mount", f"type=volume,src={volume},dst=/cache"]
    # Optional local vendor directory, e.g. for an offline rehearsal. Cargo checks checksums.
    vendor = os.environ.get("RUST_BUILD_VENDOR_DIR")
    if vendor:
        vendor_path = Path(vendor).resolve(strict=True)
        if not vendor_path.is_dir() or "," in str(vendor_path):
            raise ValueError("invalid RUST_BUILD_VENDOR_DIR")
        command += ["--mount", f"type=bind,src={vendor_path},dst=/vendor,readonly", "--network", "none"]
    command += [image, "sh", "-c", script, "sh", f"/work/{source}", build["target"], build["binary"]]
    try:
        with tempfile.TemporaryFile() as archive:
            run("git", "archive", f"--prefix={source}/", tree, cwd=REPO, stdout=archive)
            archive.seek(0)
            run(*command, stdin=archive, stdout=sys.stderr)
        temporary = output / "binary.tmp"
        run("docker", "cp", f"{container}:/cache/target/{build['target']}/release/{build['binary']}", str(temporary))
        data = temporary.read_bytes()
        elf_check(data, build["target"])
        temporary.chmod(0o755)
        artifact = output / build["binary"]
        temporary.replace(artifact)
        record = {"input_id": input_id, "source_tree": tree, "image": image,
                  "target": build["target"], "item": item["name"], "local": source,
                  "destination": f"target/release/{build['binary']}", "artifact": str(artifact),
                  "sha256": digest(data), "size": len(data), "seconds": round(time.monotonic() - started, 3)}
        temporary_meta = metadata.with_suffix(".tmp")
        temporary_meta.write_text(json.dumps(record, indent=2) + "\n")
        temporary_meta.replace(metadata)
        print(f"local build: {build['binary']} {record['seconds']}s sha256={record['sha256']}", file=sys.stderr)
        return record
    finally:
        subprocess.run(["docker", "rm", "-f", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def archive_with_artifacts(index, paths, commit="HEAD"):
    records = json.loads(Path(index).read_text())
    selected = [r for r in records if r["local"] in paths]
    for record in selected:
        if capture("git", "rev-parse", f"{commit}:{record['local']}") != record["source_tree"]:
            raise ValueError("artifact does not match the deployment source tree")
    # Verify everything before writing a byte to the SSH pipeline.
    additions = [(f"{safe_path(r['local'])}/{safe_path(r['destination'])}", verified(r)) for r in selected]
    with tempfile.TemporaryFile() as source:
        run("git", "archive", commit, "--", *paths, cwd=REPO, stdout=source)
        source.seek(0)
        with tarfile.open(fileobj=source) as tar:
            entries = tar.getmembers()
            for name, _ in additions:
                for entry in entries:
                    if entry.name == name or (name.startswith(entry.name.rstrip("/") + "/") and not entry.isdir()):
                        raise ValueError(f"artifact conflicts with tracked path: {entry.name}")
            with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as destination:
                for entry in entries:
                    destination.addfile(entry, tar.extractfile(entry) if entry.isfile() else None)
                for name, data in additions:
                    entry = tarfile.TarInfo(name)
                    entry.size, entry.mode = len(data), 0o755
                    destination.addfile(entry, io.BytesIO(data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "validate", "archive"])
    parser.add_argument("--manifest", default=str(REPO / "config/sync.json"))
    parser.add_argument("--index")
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    if args.action == "archive":
        archive_with_artifacts(args.index, args.paths, args.commit)
        return
    manifest = json.loads(Path(args.manifest).read_text())
    builds = definitions(manifest)
    if args.action == "validate":
        return
    # All worktrees share this cache and its own lock; no remote/operation lock needed.
    common = Path(capture("git", "rev-parse", "--git-common-dir"))
    common = (REPO / common).resolve()
    cache = common.parent / ".local/rust-build"
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "build.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = [build_one(b, next(i for i in manifest["items"] if i["name"] == b["item"]), cache, args.strict)
                   for b in builds]
        index = Path(args.index) if args.index else REPO / ".local/local-build.json"
        index.parent.mkdir(parents=True, exist_ok=True)
        temp = index.with_suffix(".tmp")
        temp.write_text(json.dumps(records, indent=2) + "\n")
        temp.replace(index)
        print(f"local artifacts: {index}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"local build failed: {exc}")
