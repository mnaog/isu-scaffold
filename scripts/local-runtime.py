#!/usr/bin/env python3
"""Worktree-scoped local app/DB lifecycle. Never contacts competition servers."""
import argparse
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parent.parent
# config/state-log/state_log.rs writes here unless the service sets ISUSCOPE_STATE_LOG.
DEFAULT_STATE_LOG = "/var/tmp/isuscope-state.jsonl"


def project_name(repo):
    return "isu-local-" + hashlib.sha256(str(repo.resolve()).encode()).hexdigest()[:16]


def validate(config, project):
    for kind in ("volumes", "networks"):
        for value in config.get(kind, {}).values():
            if value.get("external") or (value.get("name") and not value["name"].startswith(project + "_")):
                raise ValueError(f"{kind} must belong to this worktree's Compose project")
    for service in config["services"].values():
        if any(service.get(k) for k in ("container_name", "network_mode", "privileged", "external_links")):
            raise ValueError("fixed containers, host/shared networks and privileged services are not allowed")
        for port in service.get("ports", []):
            if port.get("host_ip") != "127.0.0.1" or port.get("published") not in (None, "", "0", 0):
                raise ValueError("publish only dynamic ports on 127.0.0.1")
        for volume in service.get("volumes", []):
            if volume["type"] == "bind" and not volume.get("read_only"):
                raise ValueError("bind mounts must be read-only; use project volumes for mutable data")


def split_services(config):
    """Image-only services that others depend on (DB, cache) are kept across `up`; Compose still
    recreates them when their own config changes. The rest carry worktree code and are rebuilt."""
    services = config["services"]
    dependencies = {name for service in services.values() for name in (service.get("depends_on") or {})}
    kept = [name for name, service in services.items() if name in dependencies and "build" not in service]
    return kept, [name for name in services if name not in kept]


def state_log_sources(config):
    """Each service's state log path inside its container. An empty ISUSCOPE_STATE_LOG turns the writer off."""
    for name, service in config["services"].items():
        path = (service.get("environment") or {}).get("ISUSCOPE_STATE_LOG", DEFAULT_STATE_LOG)
        if path:
            yield name, path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["up", "check", "down", "logs", "reset", "state-log"])
    parser.add_argument("name", nargs="?", default="latest", help="state-log: saved as .local/local-runtime/state-log/NAME.jsonl")
    args = parser.parse_args()
    if not args.name.replace("-", "").replace("_", "").replace(".", "").isalnum():
        raise ValueError(f"state log name must be letters, digits, '-', '_' or '.': {args.name}")
    state = REPO / ".local/local-runtime"
    state.mkdir(parents=True, exist_ok=True)
    project = project_name(REPO)
    compose_file = REPO / "config/local/compose.yaml"
    if not compose_file.exists():
        raise ValueError("After the start: copy config/local/compose.example.yaml to compose.yaml and adapt it to the imported app")
    env = dict(os.environ)
    for key, directory in (("LOCAL_INIT_DIR", "init"), ("LOCAL_VENDOR_DIR", "empty-vendor")):
        path = Path(env.get(key, str(state / directory))).resolve()
        if key not in env:
            path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise ValueError(f"missing {key}: {path}")
        env[key] = str(path)
    command = ["docker", "compose", "--project-name", project, "--project-directory", str(REPO / "config/local"),
               "-f", str(compose_file)]

    def invoke(*arguments, capture=False, **kwargs):
        return subprocess.run(command + list(arguments), cwd=REPO, env=env, check=True,
                              stdout=subprocess.PIPE if capture else None, **kwargs)

    def check():
        rows = invoke("ps", "--all", "--format", "json", capture=True).stdout.decode().strip()
        services = json.loads(rows) if rows.startswith("[") else [json.loads(r) for r in rows.splitlines()]
        statuses = {s["Service"]: s for s in services}
        for name in config["services"]:
            status = statuses.get(name, {})
            if status.get("State") in ("exited", "dead"):
                raise RuntimeError(f"local service exited: {name}; inspect make local-logs")
            if status.get("State") != "running" or status.get("Health", "") not in ("", "healthy"):
                raise ValueError(f"local service not ready: {name}")
        endpoint = invoke("port", "app", "8080", capture=True).stdout.decode().strip()
        if not endpoint.startswith("127.0.0.1:"):
            raise ValueError("app must expose port 8080 on loopback")
        url = "http://" + endpoint + os.environ.get("LOCAL_HEALTH_PATH", "/health")
        with urllib.request.urlopen(url, timeout=3) as response:
            if response.status != 200:
                raise ValueError(f"health check failed: {url}")
        return url

    # Local lifecycle lock is independent of remote operations and other worktrees.
    with (state / "operation.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        config = json.loads(invoke("config", "--format", "json", capture=True).stdout)
        validate(config, project)
        if args.action == "up":
            timeout = int(os.environ.get("LOCAL_START_TIMEOUT", "600"))
            deadline = time.monotonic() + timeout
            kept, rebuilt = split_services(config)
            if kept:
                # Recreating these every time waited for a DB restart and healthcheck on each edit.
                try:
                    invoke("up", "-d", "--wait", "--wait-timeout", str(timeout), "--remove-orphans", *kept)
                except subprocess.CalledProcessError:
                    invoke("logs", "--tail", "60", *kept)
                    raise ValueError("local dependencies did not become healthy; inspect make local-logs")
            invoke("up", "-d", "--build", "--force-recreate", "--no-deps", "--remove-orphans", *rebuilt)
            while True:
                try:
                    url = check()
                    break
                except (ValueError, urllib.error.URLError, TimeoutError, http.client.HTTPException,
                        ConnectionError, subprocess.CalledProcessError):
                    if time.monotonic() >= deadline:
                        invoke("logs", "--tail", "60")
                        raise ValueError("local startup timed out; inspect make local-logs")
                    time.sleep(2)
            record = {"project": project, "url": url, "worktree": str(REPO),
                      "data_level": os.environ.get("LOCAL_DATA_LEVEL", "unspecified"),
                      "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO).decode().strip(),
                      "dirty": bool(subprocess.check_output(["git", "status", "--porcelain", "--", "webapp"], cwd=REPO))}
            (state / "status.json").write_text(json.dumps(record, indent=2) + "\n")
            print(json.dumps(record, ensure_ascii=False))
        elif args.action == "check":
            print(check())
        elif args.action == "logs":
            invoke("logs", "--tail", "100")
        elif args.action == "state-log":
            # The writer flushes every 100ms. A container that was recreated (local-up) starts a new file.
            time.sleep(0.5)
            target = state / "state-log" / f"{args.name}.jsonl"
            target.parent.mkdir(parents=True, exist_ok=True)
            parts = []
            for service, path in state_log_sources(config):
                copy = target.with_name(f".{args.name}.{service}.jsonl")
                copy.unlink(missing_ok=True)
                result = subprocess.run(command + ["cp", f"{service}:{path}", str(copy)], cwd=REPO, env=env,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if result.returncode == 0 and copy.is_file():
                    parts.append((service, copy))
            if not parts:
                raise ValueError("no service has a state log yet: the app needs state_log.rs and a state change "
                                 f"after the last local-up (looked for ISUSCOPE_STATE_LOG or {DEFAULT_STATE_LOG})")
            with target.open("wb") as out:
                for _, copy in parts:
                    out.write(copy.read_bytes())
                    copy.unlink()
            lines = sum(1 for _ in target.open("rb"))
            print(json.dumps({"path": str(target), "services": [service for service, _ in parts], "lines": lines}))
        else:
            if args.action == "reset" and os.environ.get("LOCAL_RESET") != "yes":
                raise ValueError("LOCAL_RESET=yes make local-reset removes only this worktree's DB and build volumes")
            invoke("down", "--remove-orphans", *(["--volumes"] if args.action == "reset" else []))
            (state / "status.json").unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, RuntimeError, OSError, http.client.HTTPException, subprocess.CalledProcessError) as exc:
        sys.exit(f"local runtime: {exc}")
