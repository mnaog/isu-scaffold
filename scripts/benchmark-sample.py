#!/usr/bin/env python3
"""Turn a run's saved benchmark output into the adapter's verified sample.

The first benchmark of a contest runs before anyone has seen its output, so the adapter leaves
the result pending (.isuscope/benchmark.sh). This script takes that run's saved output and does
the steps that otherwise follow by hand:

  python3 scripts/benchmark-sample.py RUN
      Show the lines that carry the result and what the current BENCHMARK_*_REGEX read from them.
      Fix the regexes in config/benchmark.env until the read is right.

  python3 scripts/benchmark-sample.py RUN --apply --score N --pass true|false
      N and true/false are what you read in the output yourself, not what the regex read: they
      are the check that the regex picks the final result and not a tag or a running total. The
      script then writes the sample (participant lines only: no operator lines, no adapter
      result line), sets BENCHMARK_SAMPLE_SCORE/PASS, reseals config/benchmark-contract.json,
      runs `.isuscope/benchmark.sh --check`, and rescores RUN if it is pending.

It never starts a benchmark.
"""
import argparse
import glob
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib

ROOT = Path(os.environ.get('REPO_DIR') or Path(__file__).resolve().parent.parent)
ENV_FILE = ROOT / 'config/benchmark.env'
RESULT_WORDS = re.compile(r'score|pass|fail|スコア|成功|失敗|エラー|error', re.IGNORECASE)


def env_values():
    """The KEY=value lines of config/benchmark.env, unquoted the way bash would."""
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        match = re.match(r'^([A-Z_][A-Z0-9_]*)=(.*)$', line)
        if match:
            words = shlex.split(match.group(2), comments=False) or ['']
            values[match.group(1)] = words[0]
    return values


def run_output(run):
    """benchmark-stdout.zst of the run whose id ends with RUN (isuscope's short id is the last 8)."""
    data_dirs = ['isuscope-data']
    config = ROOT / '.isuscope/config.toml'
    if config.exists():
        data_dir = tomllib.loads(config.read_text()).get('data_dir')
        if data_dir:
            data_dirs.insert(0, data_dir)
    for data_dir in data_dirs:
        matches = sorted(glob.glob(str(ROOT / data_dir / 'runs' / f'*{run}' / 'logs/benchmark-stdout.zst')))
        if len(matches) == 1:
            return Path(matches[0])
        if len(matches) > 1:
            sys.exit(f'{run} matches several runs; give more of the id')
    sys.exit(f'no saved benchmark output for run {run} (looked under {", ".join(data_dirs)})')


def participant_lines(path):
    text = subprocess.run(['zstd', '-dcq', '--', str(path)], check=True, capture_output=True).stdout.decode(errors='replace')
    pattern = env_values().get('BENCHMARK_EXCLUDE_REGEX') or json.loads(
        (ROOT / 'config/isuscope-policy.json').read_text()).get('operator_line_pattern', '')
    operator = re.compile(pattern) if pattern else None
    # The adapter's own result line is appended to the saved output; it is not the benchmark's.
    return [line for line in text.splitlines()
            if '"type":"isuscope.result"' not in line and not (operator and operator.search(line))]


def read(lines):
    with tempfile.NamedTemporaryFile('w', suffix='.log', delete=False) as handle:
        handle.write('\n'.join(lines) + '\n')
        name = handle.name
    try:
        completed = subprocess.run(['bash', str(ROOT / '.isuscope/benchmark.sh'), '--read', name],
                                   capture_output=True, text=True, env=dict(os.environ, ISUSCOPE_PROJECT_ROOT=str(ROOT)))
    finally:
        os.unlink(name)
    if completed.returncode != 0:
        sys.exit('the adapter could not read the output: ' + completed.stderr.strip())
    return json.loads(completed.stdout)


def set_env(updates):
    lines = ENV_FILE.read_text().splitlines()
    for key, value in updates.items():
        line = f'{key}={shlex.quote(value)}'
        index = next((i for i, text in enumerate(lines) if text.startswith(key + '=')), None)
        if index is None:
            lines.append(line)
        else:
            lines[index] = line
    ENV_FILE.write_text('\n'.join(lines) + '\n')


def sourced(command):
    """Run a command with config/benchmark.env (and the local secrets) in its environment."""
    script = ('set -a; . config/benchmark.env; [ -f .local/benchmark-secrets.env ] && . .local/benchmark-secrets.env; '
              'set +a; exec ' + ' '.join(shlex.quote(word) for word in command))
    return subprocess.run(['bash', '-c', script], cwd=ROOT, capture_output=True, text=True)


def run_state(run):
    completed = subprocess.run(['isuscope', 'list', '--limit', '200'], cwd=ROOT, capture_output=True, text=True)
    if completed.returncode != 0:
        return None, None
    runs = json.loads(completed.stdout)['runs']
    for row in runs['rows']:
        values = dict(zip(runs['columns'], row))
        if values.get('id', '').endswith(run):
            return values['id'], values.get('state')
    return None, None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('run', help='run id or its last characters (isuscope list shows the last 8)')
    parser.add_argument('--apply', action='store_true', help='write the sample, reseal, check and rescore')
    parser.add_argument('--score', type=int, help='the final score you read in the output')
    parser.add_argument('--pass', dest='passed', choices=['true', 'false'], help='the final result you read in the output')
    args = parser.parse_args()

    source = run_output(args.run)
    lines = participant_lines(source)
    result = read(lines)
    print(f'output: {source.relative_to(ROOT)} ({len(lines)} participant lines)')
    print('lines that may carry the result (last 20):')
    for line in [line for line in lines if RESULT_WORDS.search(line)][-20:]:
        print('  ' + line[:200])
    print(f'the current regexes read: score={json.dumps(result["score"])} pass={json.dumps(result["pass"])}')
    print(f'  score line:  {result["score_line"] or "(none)"}')
    print(f'  result line: {result["result_line"] or "(none)"}')
    if not args.apply:
        print('\nIf that is the final result, run again with --apply --score N --pass true|false using what you read above;'
              ' otherwise fix BENCHMARK_SCORE_REGEX / BENCHMARK_PASS_REGEX / BENCHMARK_FAIL_REGEX in config/benchmark.env first.')
        return 0

    if args.score is None or args.passed is None:
        parser.error('--apply needs --score and --pass as read in the output')
    if result['score'] != args.score or result['pass'] != (args.passed == 'true'):
        sys.exit(f'the regexes read score={json.dumps(result["score"])} pass={json.dumps(result["pass"])}, '
                 f'not score={args.score} pass={args.passed}; '
                 'fix them in config/benchmark.env first')
    sample = env_values().get('BENCHMARK_SAMPLE_FILE') or 'config/benchmark-sample.log'
    (ROOT / sample).write_text('\n'.join(lines) + '\n')
    set_env({'BENCHMARK_SAMPLE_SCORE': str(args.score), 'BENCHMARK_SAMPLE_PASS': args.passed})
    print(f'wrote {sample} and BENCHMARK_SAMPLE_SCORE={args.score} BENCHMARK_SAMPLE_PASS={args.passed}')
    # The regexes and sample values are part of the sealed settings, so the contract follows them.
    sealed = sourced(['python3', 'scripts/benchmark-contract.py', '--seal'])
    if sealed.returncode != 0:
        sys.exit(sealed.stderr.strip() or sealed.stdout.strip())
    print(sealed.stdout.strip())
    checked = subprocess.run(['bash', str(ROOT / '.isuscope/benchmark.sh'), '--check'], cwd=ROOT, capture_output=True,
                             text=True, env=dict(os.environ, ISUSCOPE_PROJECT_ROOT=str(ROOT)))
    if checked.returncode != 0 or 'not verified' in checked.stderr:
        sys.exit('.isuscope/benchmark.sh --check did not pass:\n' + checked.stderr.strip())
    print(checked.stdout.strip())
    run_id, state = run_state(args.run)
    if state == 'pending':
        rescored = subprocess.run(['isuscope', 'rescore', run_id], cwd=ROOT, capture_output=True, text=True)
        print(rescored.stdout.strip() or rescored.stderr.strip())
        if rescored.returncode != 0:
            return rescored.returncode
    elif run_id:
        print(f'run {run_id[-8:]} is {state}; nothing to rescore')
    print('commit config/benchmark.env, ' + sample + ' and config/benchmark-contract.json')
    return 0


if __name__ == '__main__':
    sys.exit(main())
