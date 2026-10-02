#!/usr/bin/env python3
"""Optional Docker smoke test: two isolated worktrees, ports and volume lifecycles."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def action(repo, operation, **env):
    return subprocess.run(["python3", "scripts/local-runtime.py", operation], cwd=repo,
                          env=dict(os.environ, LOCAL_START_TIMEOUT="30", **env), check=True,
                          stdout=subprocess.PIPE).stdout.decode()


with tempfile.TemporaryDirectory() as directory:
    repos = []
    try:
        for name in ("first", "second"):
            repo = Path(directory) / name
            for folder in ("scripts", "config/local", "webapp"):
                (repo / folder).mkdir(parents=True)
            shutil.copy2(ROOT / "scripts/local-runtime.py", repo / "scripts/local-runtime.py")
            config = {"services": {"app": {"image": "python:3.12-alpine", "command": ["sh", "-c",
                      f"mkdir -p /www; echo {name} >/www/health; touch /data/{name}; exec python -m http.server 8080 --directory /www"],
                      "ports": ["127.0.0.1::8080"], "volumes": ["data:/data"]}}, "volumes": {"data": {}}}
            (repo / "config/local/compose.yaml").write_text(json.dumps(config))
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                            "commit", "--allow-empty", "-qm", "fixture"], check=True)
            repos.append(repo)
            action(repo, "up")
        records = [json.loads((r / ".local/local-runtime/status.json").read_text()) for r in repos]
        assert records[0]["project"] != records[1]["project"]
        assert records[0]["url"] != records[1]["url"]
        for record, expected in zip(records, (b"first", b"second")):
            with urllib.request.urlopen(record["url"]) as response:
                assert response.read().strip() == expected
        action(repos[0], "down")
        subprocess.run(["docker", "volume", "inspect", records[0]["project"] + "_data"],
                       check=True, stdout=subprocess.DEVNULL)
        action(repos[1], "check")
        action(repos[0], "up")
        # A normal down preserves the first worktree's volume.
        result = subprocess.check_output(["docker", "ps", "-q", "--filter",
                                          "label=com.docker.compose.project=" + records[0]["project"]]).decode().strip()
        subprocess.run(["docker", "exec", result, "test", "-f", "/data/first"], check=True)
        print("two-project runtime isolation, dynamic ports, health and lifecycle passed")
    finally:
        for repo in repos:
            action(repo, "reset", LOCAL_RESET="yes")
