#!/usr/bin/env python3
"""Check a configuration draft against the live nodes before it is applied.

Each node is visited once over SSH to collect facts (path types, owners, sizes, services,
log readability); the judgement happens locally so the rules stay testable. The result is
written to <draft>/review.md. Exit status 1 means at least one FAIL: the draft must be
fixed before `make kickoff-apply`. WARN lines need an explicit human or agent decision.
"""
import json
import re
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DANGEROUS_REMOTE = {"/", "/etc", "/home", "/home/isucon", "/opt", "/root", "/srv", "/tmp", "/usr", "/var"}
LARGE_ITEM_KB = 512 * 1024
NGINX_DROPIN = "/etc/nginx/conf.d/00-isuscope-log.conf"


def load(path: Path):
    with path.open() as source:
        return json.load(source)


def overlaps(first: str, second: str) -> bool:
    first, second = first.rstrip("/"), second.rstrip("/")
    return first == second or second.startswith(first + "/") or first.startswith(second + "/")


def group_nodes(group: str, application: list[str], roles: dict[str, list[str]]) -> list[str]:
    if group == "application":
        return application
    if group.startswith("role_"):
        role = group.removeprefix("role_")
        return [node for node in application if role in roles.get(node, [])]
    return []


def service_name(command: str) -> str | None:
    words = shlex.split(command)
    if "is-active" not in words:
        return None
    candidates = [word for word in words[words.index("is-active") + 1:] if not word.startswith("-")]
    return candidates[0] if candidates else None


def remote_script(checks: dict) -> str:
    lines = ["set -u"]
    for path in sorted(checks["paths"]):
        quoted = shlex.quote(path)
        lines.append(
            f"if sudo -n test -d {quoted}; then t=directory; elif sudo -n test -f {quoted}; then t=file; "
            f"else t=missing; fi; kb=$(sudo -n du -sk {quoted} 2>/dev/null | cut -f1); kb=${{kb:-0}}; "
            # The item's exclude (Rust target/, .git) is neither imported nor deployed, so it does
            # not count toward the size warning either.
            + "".join(f"x=$(sudo -n du -sk {shlex.quote(path + '/' + excluded)} 2>/dev/null | cut -f1); kb=$((kb - ${{x:-0}})); "
                      for excluded in sorted(checks["excludes"].get(path, ())))
            + f"printf 'path\\t%s\\t%s\\t%s\\n' {quoted} \"$t\" \"$kb\""
        )
    for user in sorted(checks["users"]):
        quoted = shlex.quote(user)
        lines.append(f"if id -u {quoted} >/dev/null 2>&1; then r=yes; else r=no; fi; printf 'user\\t%s\\t%s\\n' {quoted} \"$r\"")
    for group in sorted(checks["groups"]):
        quoted = shlex.quote(group)
        lines.append(f"if getent group {quoted} >/dev/null 2>&1; then r=yes; else r=no; fi; printf 'group\\t%s\\t%s\\n' {quoted} \"$r\"")
    for service in sorted(checks["services"]):
        quoted = shlex.quote(service)
        lines.append(f"r=$(systemctl is-active {quoted} 2>/dev/null || true); printf 'service\\t%s\\t%s\\n' {quoted} \"${{r:-unknown}}\"")
    for path in sorted(checks["dropins"]):
        quoted = shlex.quote(path)
        lines.append(f"if sudo -n test -f {quoted}; then r=yes; else r=no; fi; printf 'dropin\\t%s\\t%s\\n' {quoted} \"$r\"")
    for path in sorted(checks["logs"]):
        quoted = shlex.quote(path)
        lines.append(f"if sudo -n test -r {quoted}; then r=yes; else r=no; fi; printf 'log\\t%s\\t%s\\n' {quoted} \"$r\"")
    return "\n".join(lines)


def server_access_logs(config: str) -> list[tuple[str, str]]:
    """access_log directives inside server blocks of `nginx -T` output.

    Any server-level access_log, including `off`, replaces the http-level measurement log
    for that server, so its requests would be missing from the LTSV log.
    """
    found = []
    current_file = "-"
    stack: list[str] = []
    words: list[str] = []
    for line in config.splitlines():
        marker = re.match(r"^# configuration file (.+):$", line)
        if marker:
            current_file = marker.group(1)
            stack, words = [], []
            continue
        line = line.split("#", 1)[0]
        for token in re.findall(r"[{};]|[^\s{};]+", line):
            if token == "{":
                stack.append(words[0] if words else "")
                words = []
            elif token == "}":
                if stack:
                    stack.pop()
                words = []
            elif token == ";":
                if words and words[0] == "access_log" and "server" in stack:
                    found.append((current_file, " ".join(words[1:])))
                words = []
            else:
                words.append(token)
    return found


def nginx_config(ssh_node: str, node: str) -> str:
    try:
        completed = subprocess.run(
            [ssh_node, node, "sudo -n nginx -T 2>/dev/null"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def collect(ssh_node: str, node: str, checks: dict) -> tuple[str, dict, str | None]:
    try:
        completed = subprocess.run(
            [ssh_node, node, remote_script(checks)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return node, {}, str(error)
    if completed.returncode != 0:
        return node, {}, (completed.stderr.strip() or f"exit {completed.returncode}")
    facts = {}
    for line in completed.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            facts[(parts[0], parts[1])] = parts[2:]
    return node, facts, None


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    if len(sys.argv) not in (1, 4):
        print(f"usage: {sys.argv[0]} [INVENTORY DRAFT_DIR SSH_NODE]", file=sys.stderr)
        return 2
    if len(sys.argv) == 4:
        inventory_path, draft_dir, ssh_node = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    else:
        inventory_path = repo / ".local/ansible-inventory.json"
        draft_dir = repo / ".local/draft"
        ssh_node = str(repo / "scripts/ssh-node.sh")
    results: list[tuple[str, str]] = []

    def record(level: str, message: str) -> None:
        results.append((level, message))

    drafts = {}
    for name in ["sync.json", "node-overrides.json", "ansible-vars.json", "isuscope.json"]:
        try:
            drafts[name] = load(draft_dir / name)
        except (OSError, json.JSONDecodeError) as error:
            record("FAIL", f"{name} is missing or invalid: {error}")
    if len(drafts) < 4:
        return write(draft_dir, results)
    sync, overrides, isuscope = drafts["sync.json"], drafts["node-overrides.json"], drafts["isuscope.json"]
    # A planned switch to the Rust implementation (configure-draft.py) starts the unit at apply time.
    switch_path = draft_dir / "service-switch.json"
    switch = load(switch_path) if switch_path.is_file() else None
    inventory = load(inventory_path)
    application = sorted(inventory["all"]["children"]["application"]["hosts"])
    roles = {entry["name"]: entry.get("roles", []) for entry in overrides}

    try:
        summary = load(draft_dir / "summary.json")
    except (OSError, json.JSONDecodeError):
        summary = {}
    for warning in summary.get("warnings", []):
        record("WARN", f"draft: {warning}")

    for node in application:
        if not roles.get(node):
            record("FAIL", f"{node} has no roles in node-overrides.json")
    for role in ("nginx", "db"):
        if not any(role in node_roles for node_roles in roles.values()):
            record("WARN", f"no node has the `{role}` role; its collectors and commands will target nothing")

    items = sync.get("items", [])
    checks = {node: {"paths": set(), "users": set(), "groups": set(), "services": set(), "logs": set(), "dropins": set(), "excludes": {}} for node in application}
    names = [item.get("name") for item in items]
    if len(names) != len(set(names)):
        record("FAIL", "sync item names are not unique")
    for index, item in enumerate(items):
        for other in items[index + 1:]:
            if overlaps(item["remote"], other["remote"]):
                record("FAIL", f"remote paths overlap: {item['name']} {item['remote']} / {other['name']} {other['remote']}")
            if overlaps(item["local"], other["local"]):
                record("FAIL", f"local paths overlap: {item['name']} {item['local']} / {other['name']} {other['local']}")
    for item in items:
        remote = item["remote"].rstrip("/") or "/"
        if remote in DANGEROUS_REMOTE:
            record("FAIL", f"{item['name']} would replace a broad system path: {remote}")
        nodes = group_nodes(item["node_group"], application, roles)
        if not nodes:
            record("FAIL", f"{item['name']} targets node group {item['node_group']} with no nodes")
        source = item.get("source_node", sync.get("source_node"))
        if source not in nodes:
            record("FAIL", f"{item['name']} source node {source} is not in {item['node_group']}")
        for node in nodes:
            checks[node]["paths"].add(item["remote"])
            if item.get("exclude"):
                checks[node]["excludes"][item["remote"]] = tuple(item["exclude"])
            checks[node]["users"].add(item["owner"])
            checks[node]["groups"].add(item["owner_group"])
    for key in ("post_deploy_commands", "status_commands"):
        for command in sync.get(key, []):
            group, text = ("application", command) if isinstance(command, str) else (command["node_group"], command["command"])
            service = service_name(text)
            if service:
                for node in group_nodes(group, application, roles):
                    checks[node]["services"].add(service)
    nginx_log = isuscope.get("ISUSCOPE_NGINX_ACCESS_LOG")
    slow_log = isuscope.get("ISUSCOPE_MYSQL_SLOW_LOG") if slow_log_enabled(repo, drafts["ansible-vars.json"]) else None
    for node in application:
        if nginx_log and ("nginx" in roles.get(node, []) or "edge" in roles.get(node, [])):
            checks[node]["logs"].add(nginx_log)
            checks[node]["dropins"].add(NGINX_DROPIN)
        if slow_log and ("db" in roles.get(node, []) or "mysql" in roles.get(node, [])):
            checks[node]["logs"].add(slow_log)

    def collect_node(node: str) -> tuple[str, dict, str | None, str]:
        # nginx -T rides in the same per-node worker, so no node waits for another's SSH.
        node, facts, error = collect(ssh_node, node, checks[node])
        nginx = nginx_config(ssh_node, node) if checks[node]["dropins"] and not error else ""
        return node, facts, error, nginx

    with ThreadPoolExecutor(max_workers=5) as pool:
        collected = list(pool.map(collect_node, application))
    for node, facts, error, nginx in collected:
        if error:
            record("FAIL", f"{node}: cannot collect facts over SSH: {error}")
            continue
        for item in items:
            if node not in group_nodes(item["node_group"], application, roles):
                continue
            kind, size = (facts.get(("path", item["remote"])) or ["missing", "0"])[:2]
            if kind == "missing":
                record("FAIL", f"{node}: {item['name']} remote path does not exist: {item['remote']}")
            elif kind != item["type"]:
                record("FAIL", f"{node}: {item['name']} is a {kind}, draft says {item['type']}: {item['remote']}")
            elif int(size or 0) > LARGE_ITEM_KB:
                record("WARN", f"{node}: {item['name']} is {int(size) // 1024} MiB; deploy only the files the app needs")
            else:
                record("OK", f"{node}: {item['name']} {kind} {item['remote']} ({int(size or 0) // 1024} MiB)")
            for label, value in (("user", item["owner"]), ("group", item["owner_group"])):
                if (facts.get((label, value)) or ["no"])[0] != "yes":
                    record("FAIL", f"{node}: {item['name']} owner {label} does not exist: {value}")
        for service in sorted(checks[node]["services"]):
            state = (facts.get(("service", service)) or ["unknown"])[0]
            switching = (switch and node in switch.get("nodes", [])
                         and service.removesuffix(".service") == switch["start"].removesuffix(".service"))
            if state != "active" and switching:
                record("WARN", f"{node}: service {service} is {state}; kickoff-apply starts it and stops "
                       f"{', '.join(switch['stop']) or 'nothing'} (service-switch.json)")
            else:
                record("OK" if state == "active" else "FAIL", f"{node}: service {service} is {state}")
        if checks[node]["dropins"]:
            for file, value in server_access_logs(nginx):
                record("WARN", f"{node}: server block in {file} sets access_log {value}; its requests are not written to the isuscope LTSV log")
        for dropin in sorted(checks[node]["dropins"]):
            if (facts.get(("dropin", dropin)) or ["no"])[0] != "yes":
                record("FAIL", f"{node}: measurement drop-in is missing: {dropin} (nginx.conf must include conf.d inside http; rerun ./scripts/bootstrap.sh)")
        for log in sorted(checks[node]["logs"]):
            readable = (facts.get(("log", log)) or ["no"])[0] == "yes"
            record("OK" if readable else "WARN", f"{node}: log {'readable' if readable else 'not readable'}: {log}")
    return write(draft_dir, results)


def slow_log_enabled(repo: Path, ansible_vars: dict) -> bool:
    """Whether bootstrap turns the MySQL slow log on (the draft's vars override group_vars)."""
    if "observability_mysql_slow_log" in ansible_vars:
        return bool(ansible_vars["observability_mysql_slow_log"])
    try:
        text = (repo / "ansible/playbooks/group_vars/all.yml").read_text()
    except OSError:
        return False
    match = re.search(r"^observability_mysql_slow_log:\s*(\S+)", text, re.M)
    return bool(match) and match.group(1).lower() in ("true", "yes")


def write(draft_dir: Path, results: list[tuple[str, str]]) -> int:
    order = {"FAIL": 0, "WARN": 1, "OK": 2}
    results.sort(key=lambda result: order[result[0]])
    counts = {level: sum(1 for result in results if result[0] == level) for level in order}
    lines = [
        "# Draft review",
        "",
        f"FAIL {counts['FAIL']} / WARN {counts['WARN']} / OK {counts['OK']}",
        "",
        "FAILを直すまで`make kickoff-apply`は進みません。WARNは内容を確認して判断してください。",
        "",
    ]
    lines += [f"- {level} {message}" for level, message in results]
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "review.md").write_text("\n".join(lines) + "\n")
    for level, message in results:
        if level != "OK":
            print(f"{level} {message}")
    print(f"draft review: FAIL {counts['FAIL']} / WARN {counts['WARN']} / OK {counts['OK']} ({draft_dir / 'review.md'})")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
