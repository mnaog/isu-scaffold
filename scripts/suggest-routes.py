#!/usr/bin/env python3
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path


SEGMENTS = (
    (re.compile(r"^[0-9]+$"), ":id", r"[0-9]+"),
    (re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}$"), ":uuid", r"[0-9A-Fa-f-]+"),
    (re.compile(r"^[0-9a-fA-F]{16,}$"), ":key", r"[0-9A-Fa-f]+"),
    (re.compile(r"^[A-Za-z0-9_-]{24,}$"), ":key", r"[A-Za-z0-9_-]+"),
)


def normalize(route: str):
    canonical = []
    pattern = []
    changed = False
    for segment in route.split("/")[1:]:
        replacement = None
        for matcher, label, expression in SEGMENTS:
            if matcher.fullmatch(segment):
                replacement = (label, expression)
                break
        if replacement:
            canonical.append(replacement[0])
            pattern.append(replacement[1])
            changed = True
        else:
            canonical.append(segment)
            pattern.append(re.escape(segment))
    return "/" + "/".join(canonical), "^/" + "/".join(pattern) + "$", changed


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} RUN OUTPUT", file=sys.stderr)
        return 2
    run, output = sys.argv[1], Path(sys.argv[2])
    process = subprocess.run(
        ["isuscope", "series", run, "--metric", "http.requests", "--bucket", "3600"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    report = json.loads(process.stdout)
    candidates = defaultdict(set)
    for row in report.get("rows", []):
        route = row.get("labels", {}).get("route")
        if not route:
            continue
        canonical, pattern, changed = normalize(route)
        if changed:
            candidates[(pattern, canonical)].add(route)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w") as destination:
        destination.write("# Generated candidates. Review before copying to .isuscope/routes.toml.\n")
        for (pattern, canonical), examples in sorted(candidates.items()):
            destination.write(f"\n# examples: {', '.join(sorted(examples)[:3])}\n")
            destination.write("[[routes]]\n")
            destination.write(f"pattern = {json.dumps(pattern)}\n")
            destination.write(f"replace = {json.dumps(canonical)}\n")
    print(f"route suggestions: {output} ({len(candidates)} rules)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
