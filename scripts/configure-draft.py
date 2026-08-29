#!/usr/bin/env python3
import json
import os
import sys
from collections import Counter
from pathlib import Path


ROLE_RULES = {
    "nginx": ("nginx", "edge"),
    "mysql": ("mysql", "db"),
    "mariadb": ("mysql", "db"),
    "postgres": ("postgres", "db"),
    "redis": ("redis", "cache"),
    "pdns": ("dns",),
    "named": ("dns",),
}
SERVICE_TOKENS = tuple(ROLE_RULES) + ("isu",)
MANIFEST_NAMES = {
    "go.mod",
    "Cargo.toml",
    "package.json",
    "Gemfile",
    "requirements.txt",
    "pom.xml",
    "build.gradle",
}
OBSERVABILITY_TOOLS = ("sar", "perf", "alp", "slp", "stackcollapse-perf.pl", "flamegraph.pl", "offcputime-bpfcc")


def read_json(path: Path):
    with path.open() as source:
        return json.load(source)


def most_common(values: list[str]) -> str | None:
    if not values:
        return None
    return Counter(values).most_common(1)[0][0]


def infer_roles(report: dict) -> list[str]:
    haystack = "\n".join(
        report.get("running_services", []) + report.get("processes", [])
    ).lower()
    roles = {"app"}
    for needle, inferred in ROLE_RULES.items():
        if needle in haystack:
            roles.update(inferred)
    if report.get("versions", {}).get("perf"):
        roles.add("perf")
    return sorted(roles, key=lambda role: (role != "app", role))


def relevant_services(report: dict) -> set[str]:
    result = set()
    for service in report.get("running_services", []):
        lowered = service.lower()
        if any(token in lowered for token in SERVICE_TOKENS):
            result.add(service.removesuffix(".service"))
    return result


def application_root(reports: list[dict]) -> str | None:
    roots = []
    for report in reports:
        report_roots = []
        for candidate in report.get("application_candidates", []):
            path = Path(candidate)
            if path.name in MANIFEST_NAMES:
                report_roots.append(str(path.parent))
        if report_roots:
            common = os.path.commonpath(report_roots)
            if common not in ("/", "/home", "/opt", "/srv"):
                roots.append(common)
            else:
                roots.extend(report_roots)
    if not roots:
        return None
    counts = Counter(roots)
    return sorted(counts, key=lambda path: (-counts[path], "/webapp" not in path, len(path), path))[0]


def application_owner(reports: list[dict], root: str | None) -> tuple[str, str] | None:
    if not root:
        return None
    owners = []
    root_path = Path(root)
    for report in reports:
        for line in report.get("application_candidate_ownership", []):
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            path, owner, group = parts
            try:
                Path(path).relative_to(root_path)
            except ValueError:
                continue
            owners.append((owner, group))
    if not owners:
        return None
    return Counter(owners).most_common(1)[0][0]


def main() -> int:
    if len(sys.argv) != 4:
        print(f"usage: {sys.argv[0]} INVENTORY INSPECTION_DIR OUTPUT_DIR", file=sys.stderr)
        return 2
    inventory_path, inspection_dir, output_dir = map(Path, sys.argv[1:])
    inventory = read_json(inventory_path)
    application_hosts = inventory["all"]["children"]["application"]["hosts"]
    reports = {}
    for node in application_hosts:
        report_path = inspection_dir / f"{node}.json"
        if not report_path.is_file():
            print(f"missing inspection report: {report_path}", file=sys.stderr)
            return 1
        reports[node] = read_json(report_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    discovered_path = inventory_path.parent / "discovered-nodes.json"
    discovered = read_json(discovered_path)
    roles_by_node = {node: infer_roles(report) for node, report in reports.items()}
    draft_nodes = []
    for node in discovered:
        copy = dict(node)
        if node["name"] in roles_by_node:
            copy["roles"] = roles_by_node[node["name"]]
        draft_nodes.append(copy)

    service_sets = [relevant_services(report) for report in reports.values()]
    common_services = sorted(set.intersection(*service_sets)) if service_sets else []
    observable_service_units = sorted(
        {f"{service}.service" for services in service_sets for service in services}
    )
    root = application_root(list(reports.values()))
    detected_owner = application_owner(list(reports.values()), root)
    nginx_logs = [path for report in reports.values() for path in report.get("nginx_access_logs", [])]
    mysql_logs = [path for report in reports.values() for path in report.get("mysql_slow_logs", [])]
    config_paths = [path for report in reports.values() for path in report.get("configuration_paths", [])]
    service_fragments = []
    for report in reports.values():
        for line in report.get("service_fragments", []):
            parts = line.split("\t", 1)
            if len(parts) == 2 and parts[1].startswith("/etc/systemd/system/"):
                service_fragments.append(parts[1])

    default_user = inventory["all"]["vars"]["ansible_user"]
    default_source = next(iter(application_hosts))

    def source_for_role(role: str) -> str:
        return next((node for node, roles in roles_by_node.items() if role in roles), default_source)

    items = []
    if root:
        app_owner, app_group = detected_owner or (default_user, default_user)
        items.append({
            "name": "webapp",
            "type": "directory",
            "node_group": "application",
            "local": "webapp",
            "remote": root,
            "owner": app_owner,
            "owner_group": app_group,
        })
    if "/etc/nginx/nginx.conf" in config_paths:
        items.append({
            "name": "nginx-conf",
            "type": "file",
            "node_group": "role_nginx",
            "source_node": source_for_role("nginx"),
            "local": "config/nginx/nginx.conf",
            "remote": "/etc/nginx/nginx.conf",
            "owner": "root",
            "owner_group": "root",
        })
    mysql_root = most_common([path for path in config_paths if path in ("/etc/mysql", "/etc/my.cnf")])
    if mysql_root:
        items.append({
            "name": "mysql-conf",
            "type": "directory" if mysql_root == "/etc/mysql" else "file",
            "node_group": "role_mysql",
            "source_node": source_for_role("mysql"),
            "local": "config/mysql" if mysql_root == "/etc/mysql" else "config/mysql/my.cnf",
            "remote": mysql_root,
            "owner": "root",
            "owner_group": "root",
        })

    post_commands = []
    if any("nginx" in roles for roles in roles_by_node.values()):
        post_commands += [
            {"node_group": "role_nginx", "command": "sudo nginx -t"},
            {"node_group": "role_nginx", "command": "sudo systemctl reload nginx"},
        ]
    post_commands += [f"sudo systemctl is-active --quiet {service}" for service in common_services]
    status_commands = [f"systemctl is-active {service}" for service in common_services]
    sync = {
        "source_node": default_source,
        "minimum_free_mb_after_deploy": 1024,
        "pre_deploy_command": "",
        "items": items,
        "build_commands": [],
        "post_deploy_commands": post_commands,
        "status_commands": status_commands,
    }

    fingerprint_paths = sorted(set(
        [candidate for report in reports.values() for candidate in report.get("application_candidates", [])]
        + service_fragments
    ))
    available_tools_by_node = {
        node: sorted(
            tool for tool in OBSERVABILITY_TOOLS if report.get("versions", {}).get(tool)
        )
        for node, report in reports.items()
    }
    available_sets = [set(tools) for tools in available_tools_by_node.values()]
    common_tools = sorted(set.intersection(*available_sets)) if available_sets else []
    missing_tools_by_node = {
        node: sorted(set(OBSERVABILITY_TOOLS) - set(tools))
        for node, tools in available_tools_by_node.items()
    }
    ansible_vars = {
        "bootstrap_required_services": common_services,
        "isuscope_fingerprint_paths": fingerprint_paths,
        "observability_required_commands": common_tools,
    }
    isuscope_environment = {
        "ISUSCOPE_NGINX_ACCESS_LOG": most_common(nginx_logs) or "/var/log/nginx/access.log",
        "ISUSCOPE_MYSQL_SLOW_LOG": most_common(mysql_logs) or "/var/log/mysql/mysql-slow.log",
        "ISUSCOPE_SERVICE_UNITS": " ".join(observable_service_units),
    }
    warnings = []
    if not root:
        warnings.append("application root was not detected")
    elif not detected_owner:
        warnings.append("webapp owner/group defaulted to the SSH user; confirm them before applying")
    if not nginx_logs:
        warnings.append("Nginx access log was not detected; default path is only a candidate")
    if not mysql_logs:
        warnings.append("MySQL slow log was not detected; default path is only a candidate")
    if not items:
        warnings.append("no sync items were inferred")

    outputs = {
        "nodes.json": draft_nodes,
        "node-overrides.json": [
            {"name": node, "roles": roles} for node, roles in roles_by_node.items()
        ],
        "sync.json": sync,
        "ansible-vars.json": ansible_vars,
        "isuscope.json": isuscope_environment,
        "summary.json": {
            "roles_by_node": roles_by_node,
            "common_services": common_services,
            "observable_service_units": observable_service_units,
            "application_root": root,
            "nginx_access_log_candidates": sorted(set(nginx_logs)),
            "mysql_slow_log_candidates": sorted(set(mysql_logs)),
            "systemd_fragment_candidates": sorted(set(service_fragments)),
            "available_tools_by_node": available_tools_by_node,
            "missing_tools_by_node": missing_tools_by_node,
            "warnings": warnings,
        },
    }
    for name, value in outputs.items():
        with (output_dir / name).open("w") as destination:
            json.dump(value, destination, ensure_ascii=False, indent=2, sort_keys=True)
            destination.write("\n")
    print(f"configuration drafts: {output_dir}")
    for warning in warnings:
        print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
