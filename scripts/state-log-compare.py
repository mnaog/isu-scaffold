#!/usr/bin/env python3
"""Compare two isuscope state logs taken from the same fixed flow on two versions of the app.

A rewrite (moving state into memory, for example) must keep every state change the old
version made. The object IDs differ between runs, so this compares, for each kind, how many
objects went through each sequence of states (the `to` of their lines in time order).
It exits 0 when both logs agree, 1 when they differ, 2 when a log cannot be used.

    python3 scripts/state-log-compare.py .local/local-runtime/state-log/before.jsonl \\
        .local/local-runtime/state-log/after.jsonl
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys


def load(path):
    """Per kind, a Counter of state sequences; plus the counts of dropped and unreadable lines."""
    events, dropped, invalid = [], 0, 0
    for number, raw in enumerate(Path(path).read_text(errors="replace").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            invalid += 1
            continue
        if not isinstance(line, dict) or "type" in line:
            continue
        if "dropped" in line and "kind" not in line:
            dropped += int(line["dropped"])
            continue
        if not all(key in line for key in ("kind", "id", "to")):
            invalid += 1
            continue
        events.append((line.get("t", 0), line.get("seq", 0), number, line))
    events.sort(key=lambda event: event[:3])
    paths = defaultdict(list)
    for _, _, _, line in events:
        paths[(line["kind"], json.dumps(line["id"]))].append(str(line["to"]))
    sequences = defaultdict(Counter)
    for (kind, _), states in paths.items():
        sequences[kind][" > ".join(states)] += 1
    return sequences, dropped, invalid, len(events)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--ignore-kind", action="append", default=[], metavar="KIND",
                        help="leave out a kind whose difference is explained")
    args = parser.parse_args()
    logs = []
    for path in (args.before, args.after):
        if not path.is_file():
            sys.exit(f"state-log-compare: no file {path}")
        sequences, dropped, invalid, lines = load(path)
        print(f"{path}: {lines} lines, {len(sequences)} kinds" +
              (f", {dropped} dropped" if dropped else "") + (f", {invalid} unreadable" if invalid else ""))
        if dropped:
            sys.exit(f"state-log-compare: {path} lost {dropped} lines; take it again with less load")
        logs.append(sequences)
    before, after = logs
    differs = False
    for kind in sorted(set(before) | set(after)):
        if kind in args.ignore_kind:
            print(f"  {kind}: ignored")
            continue
        old, new = before.get(kind, Counter()), after.get(kind, Counter())
        if old == new:
            print(f"  {kind}: same ({sum(old.values())} objects, {len(old)} sequences)")
            continue
        differs = True
        print(f"  {kind}: DIFFERS ({sum(old.values())} objects before, {sum(new.values())} after)")
        for sequence in sorted(set(old) | set(new), key=lambda key: (-abs(old[key] - new[key]), key))[:20]:
            if old[sequence] != new[sequence]:
                print(f"    before {old[sequence]:>4}  after {new[sequence]:>4}  {sequence}")
    print("state logs differ" if differs else "state logs agree")
    return 1 if differs else 0


if __name__ == "__main__":
    sys.exit(main())
