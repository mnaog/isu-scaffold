#!/usr/bin/env python3
"""Apply/check repository-owned settings after isuscope generates its template."""
import argparse
import json
from pathlib import Path
import re
import tomllib


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
    config = tomllib.loads(text)
    if config.get('context', {}).get('agent', {}).get('history_dir') != policy['history_dir']:
        raise ValueError('isuscope agent history setting differs from config/isuscope-policy.json; run make discover')
    if config.get('benchmark', {}).get('operator_line_pattern') != policy['operator_line_pattern']:
        raise ValueError('isuscope operator line filter differs from config/isuscope-policy.json; run make discover')
    re.compile(policy['operator_line_pattern'])
    if config.get('benchmark', {}).get('parsers', []) != policy.get('benchmark_parsers', []):
        raise ValueError('benchmark parsers differ from repository policy; run make discover')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('--policy', type=Path, default=Path(__file__).resolve().parent.parent / 'config/isuscope-policy.json')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    policy = json.loads(args.policy.read_text())
    text = args.config.read_text()
    if args.check:
        check(text, policy)
    else:
        args.config.write_text(apply(text, policy))


if __name__ == '__main__':
    main()
