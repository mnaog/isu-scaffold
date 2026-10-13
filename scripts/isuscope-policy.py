#!/usr/bin/env python3
"""Apply/check repository-owned settings after isuscope generates its template."""
import argparse
import json
from pathlib import Path
import re
import tomllib


BENCHMARK_KEYS = ('initialize_start_marker', 'initialize_finish_marker', 'prepare_command', 'rescore_command',
                  'identity_command', 'cleanup_command', 'idle_timeout_seconds', 'timeout_seconds')
BENCHMARK_ENV = Path(__file__).resolve().parent.parent / 'config/benchmark.env'


def benchmark_timeout():
    """[benchmark] timeout_seconds follows BENCHMARK_TIMEOUT_SECONDS in config/benchmark.env, so
    isuscope's limit and the adapter's are one value (isuscope passes it back to the adapter)."""
    try:
        lines = BENCHMARK_ENV.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        match = re.match(r'^\s*BENCHMARK_TIMEOUT_SECONDS=[\'"]?(\d+)[\'"]?\s*$', line)
        if match and int(match.group(1)) > 0:
            return int(match.group(1))
    return None


def effective(policy):
    timeout = benchmark_timeout()
    return dict(policy, timeout_seconds=timeout) if timeout and not policy.get('timeout_seconds') else policy


def toml_value(value):
    if isinstance(value, dict):
        return '{ ' + ', '.join(f'{json.dumps(k)} = {toml_value(v)}' for k, v in value.items()) + ' }'
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(v) for v in value) + ']'
    return json.dumps(value, ensure_ascii=False)


def apply(text, policy):
    settings = {
        'context.agent': {'history_dir': policy['history_dir']},
        'benchmark': {'operator_line_pattern': policy['operator_line_pattern']},
    }
    if policy.get('benchmark_parsers'):
        settings['benchmark']['parsers'] = policy['benchmark_parsers']
    # Contest-specific initialize markers, and the commands around each benchmark: the restart
    # before it (prepare) and the re-read of a pending first result (rescore).
    for key in BENCHMARK_KEYS:
        if policy.get(key):
            settings['benchmark'][key] = policy[key]
    for section, values in settings.items():
        pattern = re.compile(r'^\[' + re.escape(section) + r'\][ \t]*$', re.M)
        match = pattern.search(text)
        if match is None:
            text += f'\n[{section}]\n'
            match = pattern.search(text)
        following = re.search(r'^\[', text[match.end():], re.M)
        end = match.end() + following.start() if following else len(text)
        body = text[match.end():end]
        for key, value in values.items():
            body = re.sub(r'^' + re.escape(key) + r'\s*=.*\n?', '', body, flags=re.M)
            body = body.rstrip()
            body += f'\n{key} = {toml_value(value)}\n'
        text = text[:match.end()] + body + text[end:]
    check(text, policy)
    return text


def check(text, policy):
    # isuscope's own rule (context.agent.history_dir): the history must live inside this repo.
    # An absolute path passed this check and was refused only when isuscope loaded the config.
    history = Path(policy['history_dir'])
    if not policy['history_dir'].strip() or history.is_absolute() or '..' in history.parts:
        raise ValueError('history_dir in config/isuscope-policy.json must be a non-empty relative path without `..`; '
                         'start the AI session inside this repo instead of pointing at another one')
    config = tomllib.loads(text)
    if config.get('context', {}).get('agent', {}).get('history_dir') != policy['history_dir']:
        raise ValueError('isuscope agent history setting differs from config/isuscope-policy.json; run make discover')
    if config.get('benchmark', {}).get('operator_line_pattern') != policy['operator_line_pattern']:
        raise ValueError('isuscope operator line filter differs from config/isuscope-policy.json; run make discover')
    re.compile(policy['operator_line_pattern'])
    if config.get('benchmark', {}).get('parsers', []) != policy.get('benchmark_parsers', []):
        raise ValueError('benchmark parsers differ from repository policy; run make discover')
    for key in BENCHMARK_KEYS:
        if policy.get(key) and config.get('benchmark', {}).get(key) != policy[key]:
            raise ValueError(f'isuscope benchmark {key} differs from config/isuscope-policy.json or config/benchmark.env; '
                             'run python3 scripts/isuscope-policy.py .isuscope/config.toml (or make discover)')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('--policy', type=Path, default=Path(__file__).resolve().parent.parent / 'config/isuscope-policy.json')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    policy = effective(json.loads(args.policy.read_text()))
    text = args.config.read_text()
    if args.check:
        check(text, policy)
    else:
        args.config.write_text(apply(text, policy))


if __name__ == '__main__':
    main()
