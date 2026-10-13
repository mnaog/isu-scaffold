#!/usr/bin/env python3
"""Set where initialize ends, from the access log or from the benchmark output.

Preferred, and possible as soon as the code is imported: the initialize request in
.isuscope/routes.toml ([initialize] method and path). isuscope then takes the end of the last
such request in the access log as the start of the load, whatever the benchmark prints.

  benchmark-markers.py suggest-route              initialize routes found in webapp/ code
  benchmark-markers.py set-route METHOD PATH      write [initialize] into .isuscope/routes.toml

The benchmark-output markers below remain for benchmarks that print clear lines.


isuscope splits each run into initialize and load only when the benchmark prints the
configured markers (initialize_start_marker / initialize_finish_marker); without them the
host, client and SQL summaries mix initialize into the load. The defaults belong to one
past contest, and practice second ran all 55 runs without a boundary. Set them once from
the saved output of the first run (config/benchmark-sample.log):

  benchmark-markers.py suggest            candidate lines (operator lines are never offered)
  benchmark-markers.py set START FINISH   write both to config/isuscope-policy.json and apply
  benchmark-markers.py check              exit 1 when the markers are unset or not in the sample

Markers match as substrings of a line, so pass text without the timestamp.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(os.environ.get('REPO_DIR') or Path(__file__).resolve().parent.parent)
POLICY = ROOT / 'config/isuscope-policy.json'
SAMPLE = ROOT / 'config/benchmark-sample.log'
ROUTES = ROOT / '.isuscope/routes.toml'
ROUTE_LITERAL = re.compile(r'"(/[A-Za-z0-9_/.-]*initiali[sz]e[A-Za-z0-9_/.-]*)"')
TIMESTAMP = re.compile(r'^\s*(?:\d{4}[-/]\d{2}[-/]\d{2}[T ])?\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\s*')
HINTS = re.compile(r'initiali[sz]e|初期化|整合性|validat|consisten|load|負荷|走行|start|開始|begin|finish|完了|終了', re.I)


def participant_lines(policy):
    pattern = re.compile(policy['operator_line_pattern']) if policy.get('operator_line_pattern') else None
    try:
        lines = SAMPLE.read_text(errors='replace').splitlines()
    except OSError:
        sys.exit(f'no benchmark sample: {SAMPLE.relative_to(ROOT)} (write the first run output there)')
    return [line for line in lines if not (pattern and pattern.search(line))]


def initialize_route():
    try:
        import tomllib
        return tomllib.loads(ROUTES.read_text()).get('initialize')
    except (OSError, ValueError):
        return None


def route_candidates():
    found = {}
    for path in sorted((ROOT / 'webapp').rglob('*')):
        if path.suffix not in {'.rs', '.go', '.rb', '.py', '.js', '.ts', '.php', '.conf'} or 'target' in path.parts:
            continue
        try:
            text = path.read_text(errors='replace')
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for match in ROUTE_LITERAL.finditer(line):
                found.setdefault(match.group(1), f'{path.relative_to(ROOT)}:{number}')
    return found


def set_route(method, path):
    if not re.fullmatch(r'[A-Z]+', method) or not re.fullmatch(r"/[A-Za-z0-9/_.~:@!$&()*+,;=%-]*", path):
        sys.exit('METHOD must be upper case (POST) and PATH a request path (/api/initialize)')
    text = ROUTES.read_text() if ROUTES.exists() else ''
    text = re.sub(r'(?m)^\[initialize\]\n(?:(?!\[).*\n?)*', '', text)
    block = f'[initialize]\nmethod = "{method}"\npath = "{path}"\n\n'
    # Top-level tables must come before [[routes]]; keep the leading comments first.
    head = re.match(r'(?s)((?:#[^\n]*\n|\n)*)', text).group(1)
    ROUTES.parent.mkdir(parents=True, exist_ok=True)
    ROUTES.write_text(head + block + text[len(head):])
    print(f'[initialize] {method} {path} written to .isuscope/routes.toml; commit it. '
          'Runs from now on take the load start from the access log.')


def position(lines, marker):
    return next((index for index, line in enumerate(lines) if marker in line), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['suggest-route', 'set-route', 'suggest', 'set', 'check'])
    parser.add_argument('markers', nargs='*')
    args = parser.parse_args()
    if args.action == 'suggest-route':
        for path, where in route_candidates().items():
            print(f'POST {path}    ({where})')
        return 0
    if args.action == 'set-route':
        if len(args.markers) != 2:
            parser.error('set-route needs METHOD and PATH')
        set_route(*args.markers)
        return 0
    policy = json.loads(POLICY.read_text())
    if args.action == 'check' and initialize_route():
        route = initialize_route()
        print(f"initialize boundary from the access log: {route.get('method')} {route.get('path')}")
        return 0
    lines = participant_lines(policy)
    if args.action == 'suggest':
        seen = set()
        for index, line in enumerate(lines, 1):
            text = TIMESTAMP.sub('', line).strip()
            if text and HINTS.search(text) and text not in seen:
                seen.add(text)
                print(f'{index:5d}  {text}')
        return 0
    if args.action == 'check':
        start, finish = policy.get('initialize_start_marker'), policy.get('initialize_finish_marker')
        if not start or not finish:
            print('no initialize boundary: python3 scripts/benchmark-markers.py suggest-route, then set-route', file=sys.stderr)
            return 1
        first, second = position(lines, start), position(lines, finish)
        if first is None or second is None or second < first:
            print('initialize markers do not appear in order in config/benchmark-sample.log', file=sys.stderr)
            return 1
        print('initialize markers found in the sample')
        return 0
    if len(args.markers) != 2 or not all(m.strip() for m in args.markers):
        parser.error('set needs START and FINISH')
    start, finish = args.markers
    first, second = position(lines, start), position(lines, finish)
    if first is None or second is None:
        sys.exit('each marker must appear in a participant line of config/benchmark-sample.log')
    if second < first:
        sys.exit('the finish marker must come after the start marker')
    policy.update(initialize_start_marker=start, initialize_finish_marker=finish)
    POLICY.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + '\n')
    config = ROOT / '.isuscope/config.toml'
    if config.exists():
        subprocess.run([sys.executable, str(ROOT / 'scripts/isuscope-policy.py'), str(config)], check=True)
    print(f'markers set (lines {first + 1} and {second + 1}); commit config/isuscope-policy.json. '
          'Runs from now on split initialize from load.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
