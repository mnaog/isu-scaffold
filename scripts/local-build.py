#!/usr/bin/env python3
"""Build committed Go sources locally and attach verified artifacts to deploy archives.

The local Go toolchain cross-compiles a static Linux binary (CGO_ENABLED=0); GOTOOLCHAIN=auto
fetches the version go.mod asks for. No Docker and no glibc matching are needed.

A build also runs the module's tests (`go test -vet=off ./...` on this machine) when it has any, and
keeps the result with the artifact: a source tree built and tested once, by `make build`, `make deploy`
or the prebuild that runs during a benchmark (scripts/prebuild.py), is neither built nor tested again.
A failing test fails the build. BUILD_TESTS=0 builds without them (recorded as skipped)."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

REPO = Path(__file__).resolve().parent.parent
TARGETS = {"linux/amd64": (62, "amd64"),
           "linux/arm64": (183, "arm64")}
FIELDS = {"item", "target", "binary", "output", "package", "source_root", "manifest_dir"}


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
        if not {"item", "target", "binary"} <= set(build) or set(build) - FIELDS:
            raise ValueError("local_builds requires item, target, binary (optional: output, package, source_root, manifest_dir)")
        item = items.get(build["item"])
        if not item or item["type"] != "directory" or build["item"] in seen:
            raise ValueError("local build requires a unique directory item")
        seen.add(build["item"])
        if any(b["item"] == build["item"] for b in manifest.get("build_commands", [])):
            raise ValueError("do not combine local and remote builds for the same item")
        safe_path(item["local"])
        source_root = safe_path(build.get("source_root", item["local"]))
        if not source_root.startswith("webapp/") and source_root != "webapp":
            raise ValueError("source_root must stay under webapp")
        if not Path(item["local"]).is_relative_to(source_root):
            raise ValueError("source_root must contain the deployed item")
        if build.get("manifest_dir", ".") != ".":
            safe_path(build["manifest_dir"])
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", build["binary"]):
            raise ValueError("invalid Go binary name")
        safe_path(build.get("output", build["binary"]))
        package = build.get("package", ".")
        if package != "." and not (package.startswith("./") and safe_path(package[2:])):
            raise ValueError("package must be . or ./<directory> inside the module")
        if build["target"] not in TARGETS:
            raise ValueError("unsupported Go target (linux/amd64 or linux/arm64)")
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


def go_command():
    """The local go; the tarball install in ~/.local/go also serves shells that skip the profile."""
    found = shutil.which("go") or str(Path.home() / ".local/go/bin/go")
    if not Path(found).is_file():
        raise ValueError("go is not installed; install the Go toolchain (docs/guides/preparation.md)")
    return found


def go_environment(cache, target):
    # 関数名のsymbolは外さない（`-ldflags=-s -w`を付けない）。perfとGoのprofileは名前で集計し、PGOも
    # 名前で関数を探す。`default.pgo`が`main` packageにあれば、`go build`の既定（`-pgo=auto`）がそれを使う。
    goos, goarch = target.split("/")
    environment = dict(os.environ, CGO_ENABLED="0", GOOS=goos, GOARCH=goarch, GOFLAGS="-trimpath",
                       GOTOOLCHAIN=os.environ.get("GOTOOLCHAIN", "auto"),
                       GOCACHE=str(cache / "gocache"), GOMODCACHE=str(cache / "gomod"))
    # x86-64-v1 runs on every node CPU; a mixed stack (c7i and c6in) must not die with SIGILL.
    environment.pop("GOAMD64", None)
    return environment


def prepare_toolchain(cache):
    """Preparation: compile the standard library for both targets before any application code exists."""
    go = go_command()
    with tempfile.TemporaryDirectory() as work:
        Path(work, "go.mod").write_text("module prepare\n")
        Path(work, "main.go").write_text('package main\n\nimport (\n\t"database/sql"\n\t"net/http"\n)\n\n'
                                         'func main() { _ = sql.Drivers(); _ = http.ListenAndServe }\n')
        for target in TARGETS:
            started = time.monotonic()
            run(go, "build", "-o", os.devnull, ".", cwd=work, env=go_environment(cache, target), stdout=sys.stderr)
            print(f"toolchain ready: {capture(go, 'version')} {target} ({time.monotonic() - started:.0f}s)", file=sys.stderr)


def extract(tree, work):
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    with tempfile.TemporaryFile() as archive:
        run("git", "archive", tree, cwd=REPO, stdout=archive)
        archive.seek(0)
        with tarfile.open(fileobj=archive) as tar:
            tar.extractall(work, filter="data")


def run_tests(go, module, cache):
    """`go test` of the extracted module on this machine; the status to keep with the artifact."""
    if os.environ.get("BUILD_TESTS") == "0":
        return {"status": "skipped"}
    if not any(path for path in module.rglob("*_test.go") if "vendor" not in path.relative_to(module).parts):
        return {"status": "none"}
    environment = {key: value for key, value in os.environ.items() if key not in ("GOOS", "GOARCH", "GOAMD64")}
    environment.update(GOFLAGS="-trimpath", GOTOOLCHAIN=os.environ.get("GOTOOLCHAIN", "auto"),
                       GOCACHE=str(cache / "gocache"), GOMODCACHE=str(cache / "gomod"))
    if (module / "vendor/modules.txt").is_file():
        environment["GOFLAGS"] += " -mod=vendor"
    started = time.monotonic()
    result = subprocess.run([go, "test", "-vet=off", "./..."], cwd=module, env=environment,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300)
    seconds = round(time.monotonic() - started, 3)
    if result.returncode != 0:
        tail = "\n".join(result.stdout.strip().splitlines()[-30:])
        raise ValueError(f"go test failed in {seconds}s (BUILD_TESTS=0 builds without the tests):\n{tail}")
    print(f"local build: tests passed ({seconds}s)", file=sys.stderr)
    return {"status": "passed", "seconds": seconds}


def build_one(build, item, cache, strict):
    if "replace-with" in build["binary"]:
        raise ValueError("After the start: configure the Go binary from the imported app before building")
    source = item["local"]
    source_root = build.get("source_root", source)
    clean(source_root)
    if strict:
        clean("scripts/local-build.py")
    tree = capture("git", "rev-parse", f"HEAD:{source_root}")
    manifest_dir = build.get("manifest_dir", ".")
    module = Path(source).relative_to(source_root) / manifest_dir
    if subprocess.run(["git", "cat-file", "-e", f"HEAD:{(Path(source_root) / module).as_posix()}/go.mod"], cwd=REPO,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        raise ValueError(f"missing go.mod in {source}/{manifest_dir}: import the module root, then set source_root/manifest_dir")
    go = go_command()
    toolchain = capture(go, "version")
    output_path = build.get("output", build["binary"])
    input_id = digest(json.dumps([tree, build, toolchain, digest(Path(__file__).read_bytes())], sort_keys=True).encode())
    output = cache / input_id
    metadata = output / "artifact.json"
    # Each build and test runs in a directory of its own, so builds of other trees (main's deploy, the
    # prebuild of a proposal just submitted) never wait for each other. -trimpath keeps Go's own caches
    # warm whatever the directory.
    (cache / "work").mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(dir=cache / "work", prefix=digest(source_root.encode())[:12] + "-"))
    try:
        return build_in(build, item, cache, tree, module, go, toolchain, input_id, output, metadata, workspace, source,
                        output_path)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def publish(staging, output):
    """Move a finished artifact directory into place; a build of the same tree that got there first wins."""
    try:
        os.rename(staging, output)
        return True
    except OSError:
        if (output / "artifact.json").exists():
            shutil.rmtree(staging, ignore_errors=True)
            return False
        shutil.rmtree(output, ignore_errors=True)  # left by an interrupted build before artifact.json
        os.rename(staging, output)
        return True


def build_in(build, item, cache, tree, module, go, toolchain, input_id, output, metadata, work, source, output_path):
    if metadata.exists():
        record = json.loads(metadata.read_text())
        if record["input_id"] != input_id:
            raise ValueError("artifact input mismatch")
        verified(record)
        tests = record.get("tests", {}).get("status")
        if tests not in ("passed", "none") and os.environ.get("BUILD_TESTS") != "0":
            # Built earlier without its tests (BUILD_TESTS=0, or before builds ran them): test it now.
            extract(tree, work)
            record["tests"] = run_tests(go, work / module, cache)
            temporary_meta = metadata.with_name(f".artifact.{os.getpid()}.tmp")
            temporary_meta.write_text(json.dumps(record, indent=2) + "\n")
            temporary_meta.replace(metadata)
        print(f"local build: cached {build['binary']} ({input_id[:12]}, tests {record.get('tests', {}).get('status', 'skipped')})",
              file=sys.stderr)
        return record
    started = time.monotonic()
    extract(tree, work)
    staging = Path(tempfile.mkdtemp(dir=cache, prefix=f".{input_id[:12]}-"))
    temporary = staging / "binary.tmp"
    environment = go_environment(cache, build["target"])
    if Path(work, module, "vendor/modules.txt").is_file():
        environment["GOFLAGS"] += " -mod=vendor"
    pgo = (work / module / build.get("package", ".") / "default.pgo").is_file()
    run(go, "build", "-o", str(temporary), build.get("package", "."), cwd=work / module, env=environment, stdout=sys.stderr)
    data = temporary.read_bytes()
    elf_check(data, build["target"])
    tests = run_tests(go, work / module, cache)
    temporary.chmod(0o755)
    temporary.replace(staging / build["binary"])
    artifact = output / build["binary"]
    record = {"input_id": input_id, "source_tree": tree, "toolchain": toolchain,
              "target": build["target"], "item": item["name"], "local": source,
              "destination": output_path, "artifact": str(artifact),
              "sha256": digest(data), "size": len(data), "seconds": round(time.monotonic() - started, 3),
              "pgo": pgo, "tests": tests}
    (staging / "artifact.json").write_text(json.dumps(record, indent=2) + "\n")
    if not publish(staging, output):
        record = json.loads(metadata.read_text())
        verified(record)
        print(f"local build: {build['binary']} was built meanwhile by another build of the same tree", file=sys.stderr)
        return record
    print(f"local build: {build['binary']} {record['seconds']}s sha256={record['sha256']}"
          f"{' with default.pgo' if pgo else ''}", file=sys.stderr)
    return record


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


def interrupted(signum, _frame):
    # Exit through Python so stopping the parallel lane also stops the go child it waits for.
    raise SystemExit(128 + signum)


def main():
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "validate", "archive", "prepare-toolchain"])
    parser.add_argument("--manifest", default=str(REPO / "config/sync.json"))
    parser.add_argument("--index")
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("paths", nargs="*")
    # Python 3.12's parse_args rejects trailing paths after options when nargs='*'.
    args = parser.parse_intermixed_args()
    if args.action == "archive":
        archive_with_artifacts(args.index, args.paths, args.commit)
        return
    if args.action != "prepare-toolchain":
        manifest = json.loads(Path(args.manifest).read_text())
        builds = definitions(manifest)
        if args.action == "validate":
            return
    # All worktrees share this cache and its own lock; no remote/operation lock needed.
    common = Path(capture("git", "rev-parse", "--git-common-dir"))
    common = (REPO / common).resolve()
    cache = common.parent / ".local/go-build"
    cache.mkdir(parents=True, exist_ok=True)
    if args.action == "prepare-toolchain":
        prepare_toolchain(cache)
        return
    records = [build_one(b, next(i for i in manifest["items"] if i["name"] == b["item"]), cache, args.strict)
               for b in builds]
    index = Path(args.index) if args.index else REPO / ".local/local-build.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    temp = index.with_name(f".{index.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(records, indent=2) + "\n")
    temp.replace(index)
    print(f"local artifacts: {index}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"local build failed: {exc}")
