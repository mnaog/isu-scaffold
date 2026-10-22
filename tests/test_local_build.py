import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import time
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("local_build", ROOT / "scripts/local-build.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

FAKE_GO = '''#!/usr/bin/env python3
import json, os, pathlib, sys, time
a = sys.argv[1:]
root = pathlib.Path(os.environ["FAKE_ROOT"])
if a == ["version"]:
    print("go version go1.99.0 fake/amd64")
    sys.exit(0)
keys = ("CGO_ENABLED", "GOOS", "GOARCH", "GOAMD64", "GOFLAGS", "GOCACHE", "GOMODCACHE", "GOTOOLCHAIN")
with (root / "go.log").open("a") as f:
    f.write(json.dumps({"args": a, "cwd": os.getcwd(), "env": {k: os.environ.get(k) for k in keys}}) + "\\n")
assert a[0] == "build" and a[1] == "-o", a
if not os.environ.get("PREPARE"):
    assert pathlib.Path("go.mod").is_file(), "go.mod missing in " + os.getcwd()
if os.environ.get("WAIT_BUILD"):
    time.sleep(30)
if os.environ.get("FAIL_BUILD"):
    sys.exit(1)
if a[2] != os.devnull:
    elf = bytearray(128)
    elf[:6] = b"\\x7fELF\\x02\\x01"
    machine = 183 if (os.environ["GOARCH"] == "arm64") != bool(os.environ.get("WRONG_ARCH")) else 62
    elf[18:20] = machine.to_bytes(2, "little")
    pathlib.Path(a[2]).write_bytes(bytes(elf))
'''

FAKE_SSH = '''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["FAKE_ROOT"])
node, command = sys.argv[1:]
with (root / "ssh.log").open("a") as f: f.write(json.dumps([node, command]) + "\\n")
if " -xf -" in command:
    (root / (node + ".tar")).write_bytes(sys.stdin.buffer.read())
    if os.environ.get("FAIL_STAGING"): sys.exit(1)
'''


class LocalBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        for folder in ("scripts", "config", "webapp/go", ".local", "bin"):
            (self.repo / folder).mkdir(parents=True)
        for script in ("local-build.py", "deploy.sh", "sync-lib.sh", "check-deployment.py", "deploy-plan.py",
                       "rollback.sh", "status.sh"):
            shutil.copy2(ROOT / "scripts" / script, self.repo / "scripts" / script)
        (self.repo / "scripts/ssh-node.sh").write_text(FAKE_SSH)
        (self.repo / "scripts/ssh-node.sh").chmod(0o755)
        (self.repo / "bin/go").write_text(FAKE_GO)
        (self.repo / "bin/go").chmod(0o755)
        (self.repo / ".gitignore").write_text(".local/\nbin/\nwebapp/go/app\n")
        (self.repo / "webapp/go/go.mod").write_text("module example.com/app\n\ngo 1.23\n")
        (self.repo / "webapp/go/main.go").write_text("package main\n\nfunc main() {}\n")
        self.manifest = {"source_node": "app1", "pre_deploy_command": "",
                         "items": [{"name": "app", "type": "directory", "node_group": "application",
                                    "local": "webapp/go", "remote": "/home/isucon/webapp/go",
                                    "owner": "isucon", "owner_group": "isucon", "exclude": ["app", ".git"]}],
                         "local_builds": [{"item": "app", "target": "linux/amd64", "binary": "app"}],
                         "build_commands": [], "post_deploy_commands": [], "status_commands": []}
        self.write_manifest()
        (self.repo / ".local/ansible-inventory.json").write_text(json.dumps(
            {"all": {"children": {"application": {"hosts": {"app1": {}, "app2": {}}}}}}))
        self.env = dict(os.environ, PATH=str(self.repo / "bin") + os.pathsep + os.environ["PATH"],
                        FAKE_ROOT=str(self.repo / ".local"), ISUSCOPE_LOCK_HELD="1")
        self.env.pop("SYNC_MANIFEST", None)
        self.env.pop("ANSIBLE_INVENTORY", None)
        self.command("git", "init", "-q")
        self.command("git", "config", "user.name", "Test")
        self.command("git", "config", "user.email", "test@example.invalid")
        self.commit()
        (self.repo / 'scripts/board').mkdir(exist_ok=True)

    def command(self, *args, ok=True, **env):
        result = subprocess.run(args, cwd=self.repo, env=dict(self.env, **env), capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr.decode())
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def write_manifest(self):
        (self.repo / "config/sync.json").write_text(json.dumps(self.manifest))

    def commit(self):
        self.command("git", "add", ".")
        self.command("git", "commit", "-qm", "fixture")

    def build(self, **kwargs):
        return self.command("python3", "scripts/local-build.py", "build", **kwargs)

    def calls(self, name):
        p = self.repo / ".local" / (name + ".log")
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def go_builds(self):
        return self.calls("go")

    def record(self):
        return json.loads((self.repo / ".local/local-build.json").read_text())[0]

    def test_a_static_build_for_every_node_cpu(self):
        # Nodes may run different CPUs (c7i and c6in); a GOAMD64 level above v1 dies with SIGILL on the other one.
        self.build(GOAMD64="v3")
        call = self.go_builds()[0]
        self.assertEqual(call["env"]["CGO_ENABLED"], "0")
        self.assertEqual((call["env"]["GOOS"], call["env"]["GOARCH"]), ("linux", "amd64"))
        self.assertIsNone(call["env"]["GOAMD64"])
        self.assertEqual(call["env"]["GOTOOLCHAIN"], "auto")
        self.assertEqual(call["args"][-1], ".")
        self.assertIn("/.local/go-build/work/", call["cwd"])

    def test_stopping_build_group_stops_the_compiler(self):
        child = subprocess.Popen(['python3', 'scripts/local-build.py', 'build'], cwd=self.repo,
                                 env=dict(self.env, WAIT_BUILD='1'), start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            while not self.go_builds():
                if time.monotonic() > deadline:
                    self.fail('fake compiler did not start')
                time.sleep(.05)
            os.killpg(child.pid, signal.SIGTERM)
            self.assertEqual(child.wait(timeout=5), 143)
            self.assertFalse((self.repo / '.local/local-build.json').exists())
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()

    def test_toolchain_preparation_builds_both_targets_into_the_shared_cache(self):
        self.command("python3", "scripts/local-build.py", "prepare-toolchain", PREPARE="1")
        prepared = self.go_builds()
        self.assertEqual(sorted(c["env"]["GOARCH"] for c in prepared), ["amd64", "arm64"])
        self.build()
        self.assertEqual(self.go_builds()[-1]["env"]["GOCACHE"], prepared[0]["env"]["GOCACHE"])

    def test_cache_reuses_artifact_and_dependencies_after_source_change(self):
        self.build()
        first = self.record()
        self.build()
        self.assertEqual(len(self.go_builds()), 1)
        (self.repo / "webapp/go/main.go").write_text("package main\n\nfunc main() { println(1) }\n")
        self.commit()
        self.build()
        self.assertNotEqual(first["input_id"], self.record()["input_id"])
        builds = self.go_builds()
        self.assertEqual(len(builds), 2)
        self.assertEqual(builds[0]["env"]["GOMODCACHE"], builds[1]["env"]["GOMODCACHE"])

    def test_output_inside_the_application(self):
        self.manifest["local_builds"][0].update(output="bin/isuride", package="./cmd/isuride")
        self.write_manifest()
        self.commit()
        self.build()
        self.assertEqual(self.go_builds()[0]["args"][-1], "./cmd/isuride")
        archive = self.command("python3", "scripts/local-build.py", "archive", "--index",
                               ".local/local-build.json", "--", "webapp/go").stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as t:
            self.assertEqual(t.getmember("webapp/go/bin/isuride").mode, 0o755)

    def test_deploy_builds_once_and_ships_identical_binary_to_two_nodes(self):
        (self.repo / "webapp/go/app").write_text("the binary built on the node; never deploy")
        self.command("bash", "scripts/deploy.sh", RELEASE="local-test")
        binaries = []
        for node in ("app1", "app2"):
            with tarfile.open(self.repo / ".local" / (node + ".tar")) as t:
                path = "webapp/go/app"
                self.assertEqual(t.getmember(path).mode, 0o755)
                binaries.append(t.extractfile(path).read())
        self.assertEqual(binaries[0], binaries[1])
        self.assertEqual(len(self.go_builds()), 1)
        commands = "\n".join(c[1] for c in self.calls("ssh"))
        self.assertNotIn("go build", commands)
        self.assertIn("sha256sum -c -", commands)
        self.assertIn("uname -m", commands)
        self.assertIn("not found", commands)

    def test_later_deploys_send_only_changed_items_and_their_commands(self):
        (self.repo / "config/nginx").mkdir(parents=True)
        (self.repo / "config/nginx/nginx.conf").write_text("worker_processes 1;\n")
        self.manifest["items"].append({"name": "nginx-conf", "type": "file", "node_group": "application",
                                       "local": "config/nginx/nginx.conf", "remote": "/etc/nginx/nginx.conf",
                                       "owner": "root", "owner_group": "root"})
        self.manifest["post_deploy_commands"] = [
            {"node_group": "application", "command": "sudo nginx -t", "when": ["nginx-*"]},
            {"node_group": "application", "command": "sudo systemctl restart app", "when": ["app"]},
            {"node_group": "application", "command": "echo always"}]
        self.manifest["rollback_commands"] = self.manifest["post_deploy_commands"]
        self.write_manifest()
        self.commit()

        def deploy(release):
            (self.repo / ".local/ssh.log").unlink(missing_ok=True)
            return self.command("bash", "scripts/deploy.sh", RELEASE=release).stdout.decode()

        def sessions():
            return [c[1] for c in self.calls("ssh")]

        # Without a recorded deploy everything goes out and every command runs.
        deploy("first")
        with tarfile.open(self.repo / ".local/app1.tar") as t:
            self.assertIn("config/nginx/nginx.conf", t.getnames())
            self.assertIn("webapp/go/app", t.getnames())
        commands = [s for s in sessions() if "echo always" in s]
        # One SSH per node runs all of that node's commands, in order.
        self.assertEqual(len(commands), 2)
        self.assertLess(commands[0].index("nginx -t"), commands[0].index("restart app"))

        # An nginx-only change sends nginx.conf, skips the Go build, and does not restart the app.
        builds = len(self.go_builds())
        (self.repo / "config/nginx/nginx.conf").write_text("worker_processes 2;\n")
        self.commit()
        output = deploy("nginx-only")
        self.assertIn("changed items: nginx-conf", output)
        with tarfile.open(self.repo / ".local/app1.tar") as t:
            self.assertEqual([m.name for m in t.getmembers() if m.isfile()], ["config/nginx/nginx.conf"])
        self.assertEqual(len(self.go_builds()), builds)
        commands = "\n".join(sessions())
        self.assertIn("nginx -t", commands)
        self.assertIn("echo always", commands)
        self.assertNotIn("restart app", commands)

        # Rolling that release back restores nginx.conf only, with its own commands.
        (self.repo / ".local/ssh.log").unlink()
        self.command("bash", "scripts/rollback.sh", "nginx-only")
        restored = "\n".join(sessions())
        self.assertIn("/etc/nginx/nginx.conf.isuscope-backup.nginx-only", restored)
        self.assertNotIn("/home/isucon/webapp/go.isuscope-backup", restored)
        self.assertNotIn("restart app", restored)

        # A commit outside the synced files sends nothing and only records the commit.
        self.command("bash", "scripts/deploy.sh", RELEASE="redo-nginx")  # back to the nginx-only state
        (self.repo / "docs").mkdir(exist_ok=True)
        (self.repo / "docs/notes.md").write_text("notes\n")
        self.commit()
        output = deploy("docs-only")
        self.assertIn("nothing to send", output)
        self.assertEqual(sessions(), [])
        head = self.command("git", "rev-parse", "HEAD").stdout.decode().strip()
        self.assertEqual((self.repo / ".local/current-deploy-commit").read_text().strip(), head)

        # DEPLOY_ALL=1 sends everything again.
        (self.repo / ".local/ssh.log").unlink(missing_ok=True)
        self.command("bash", "scripts/deploy.sh", RELEASE="all", DEPLOY_ALL="1")
        with tarfile.open(self.repo / ".local/app1.tar") as t:
            self.assertIn("webapp/go/app", t.getnames())

    def test_build_failure_never_contacts_nodes(self):
        self.command("bash", "scripts/deploy.sh", ok=False, FAIL_BUILD="1")
        self.assertEqual(self.calls("ssh"), [])

    def test_corrupt_artifact_is_rejected_before_ssh(self):
        self.build()
        Path(self.record()["artifact"]).write_bytes(b"tampered")
        self.command("bash", "scripts/deploy.sh", ok=False)
        self.assertEqual(self.calls("ssh"), [])

    def test_unconfigured_binary_stops_before_go(self):
        self.manifest["local_builds"][0]["binary"] = "replace-with-binary-name"
        self.write_manifest()
        self.build(ok=False)
        self.assertEqual(self.go_builds(), [])

    def test_wrong_architecture_is_rejected(self):
        self.build(ok=False, WRONG_ARCH="1")
        self.assertFalse((self.repo / ".local/local-build.json").exists())

    def test_stale_archive_is_rejected_without_output(self):
        self.build()
        (self.repo / "webapp/go/main.go").write_text("changed\n")
        self.commit()
        result = self.command("python3", "scripts/local-build.py", "archive", "--index",
                              ".local/local-build.json", "--", "webapp/go", ok=False)
        self.assertEqual(result.stdout, b"")

    def test_dirty_source_and_recipe_are_rejected(self):
        source = self.repo / "webapp/go/main.go"
        source.write_text("dirty\n")
        self.build(ok=False)
        self.command("git", "restore", str(source))
        with (self.repo / "scripts/local-build.py").open("a") as script:
            script.write("# dirty\n")
        self.command("bash", "scripts/deploy.sh", ok=False)
        self.assertEqual(self.calls("ssh"), [])

    def test_staging_failure_does_not_switch(self):
        self.command("bash", "scripts/deploy.sh", ok=False, FAIL_STAGING="1", RELEASE="failed-staging")
        commands = "\n".join(c[1] for c in self.calls("ssh"))
        self.assertNotIn("'; if sudo test -e", commands)
        self.assertEqual((self.repo / ".local/deploy-transactions/failed-staging.state").read_text(), "failed\n")

    def test_workspace_shared_dependency_invalidates_cache(self):
        (self.repo / 'webapp/shared').mkdir()
        dependency = self.repo / 'webapp/shared/lib.go'
        dependency.write_text('package shared\n')
        (self.repo / 'webapp/go/go.mod').write_text('module example.com/app\n\ngo 1.23\n\nreplace example.com/shared => ../shared\n')
        self.manifest['local_builds'][0].update(source_root='webapp')
        self.write_manifest()
        self.commit()
        self.build()
        first = self.record()['input_id']
        self.assertTrue(self.go_builds()[0]['cwd'].endswith('/go'))
        dependency.write_text('package shared\n\nconst Changed = true\n')
        self.commit()
        self.build()
        self.assertNotEqual(first, self.record()['input_id'])

    def test_workspace_cannot_escape_webapp(self):
        self.manifest['local_builds'][0]['source_root'] = '.local'
        with self.assertRaises(ValueError):
            builder.definitions(self.manifest)

    def test_unsafe_or_conflicting_definitions(self):
        for field, value in (("output", "../outside"), ("package", "../outside"), ("package", "cmd/app"),
                             ("target", "x86_64-unknown-linux-gnu"), ("dockerfile", "config/Dockerfile")):
            with self.subTest(**{field: value}):
                build = dict(self.manifest["local_builds"][0], **{field: value})
                with self.assertRaises(ValueError):
                    builder.definitions(dict(self.manifest, local_builds=[build]))
        self.manifest["build_commands"] = [{"item": "app"}]
        with self.assertRaises(ValueError):
            builder.definitions(self.manifest)

if __name__ == "__main__":
    unittest.main()
