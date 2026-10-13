#!/usr/bin/env python3
"""Set the benchmark-output lines that mark where initialize starts and ends.

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
TIMESTAMP = re.compile(r'^\s*(?:\d{4}[-/]\d{2}[-/]\d{2}[T ])?\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\s*')
HINTS = re.compile(r'initiali[sz]e|初期化|整合性|validat|consisten|load|負荷|走行|start|開始|begin|finish|完了|終了', re.I)


def participant_lines(policy):
    pattern = re.compile(policy['operator_line_pattern']) if policy.get('operator_line_pattern') else None
    try:
        lines = SAMPLE.read_text(errors='replace').splitlines()
    except OSError:
        sys.exit(f'no benchmark sample: {SAMPLE.relative_to(ROOT)} (write the first run output there)')
    return [line for line in lines if not (pattern and pattern.search(line))]


def position(lines, marker):
    return next((index for index, line in enumerate(lines) if marker in line), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['suggest', 'set', 'check'])
    parser.add_argument('markers', nargs='*')
    args = parser.parse_args()
    policy = json.loads(POLICY.read_text())
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
            print('initialize markers are not set: python3 scripts/benchmark-markers.py suggest, then set', file=sys.stderr)
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
