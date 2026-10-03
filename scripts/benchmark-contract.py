#!/usr/bin/env python3
"""Verify a reviewed benchmark invocation; never execute the benchmark."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys


def digest(data):
    return hashlib.sha256(data).hexdigest()


def effective(root, env):
    values = {k: v for k, v in env.items() if k.startswith('BENCHMARK_') and k not in
              {'BENCHMARK_CONTRACT_FILE', 'BENCHMARK_CONFIG_FILE', 'BENCHMARK_SECRETS_FILE'}}
    # Bind file contents as well as their names (HTTP body/header changes matter).
    for key in ('BENCHMARK_HTTP_BODY_FILE', 'BENCHMARK_HTTP_HEADERS_FILE'):
        if values.get('BENCHMARK_TRANSPORT') == 'http' and values.get(key):
            path = Path(values[key])
            values[key + '_SHA256'] = digest((root / path).read_bytes())
    return digest(json.dumps(values, sort_keys=True, separators=(',', ':')).encode())


def command_words(command):
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>()')
    lexer.whitespace_split = True
    words = list(lexer)
    # The SSH adapter commonly runs sudo ... bash -c '...'. Inspect that script too.
    for i, word in enumerate(words[:-2]):
        if Path(word).name in ('bash', 'sh') and words[i + 1] == '-c':
            return words + command_words(words[i + 2])
    return words


def check(root, contract, env, seal=True):
    if contract.get('schema_version') != 1:
        raise ValueError('contract schema_version must be 1')
    if not contract.get('reviewed_by') or not contract.get('reviewed_at'):
        raise ValueError('record the operator and review time after checking official instructions')
    conditions = contract.get('conditions', {})
    for key in ('mode', 'request_timeout', 'initialize_timeout', 'load_duration', 'target'):
        if not isinstance(conditions.get(key), str) or not conditions[key].strip():
            raise ValueError('missing reviewed condition: ' + key)
    sources = contract.get('sources', [])
    if not sources:
        raise ValueError('official source files and hashes are required')
    for source in sources:
        path = (root / source['path']).resolve()
        if not path.is_relative_to((root / 'docs/official').resolve()):
            raise ValueError('evidence must be saved under docs/official')
        if digest(path.read_bytes()) != source['sha256']:
            raise ValueError('official evidence changed: ' + source['path'])
    if env.get('BENCHMARK_TRANSPORT') in ('local', 'ssh'):
        invocation = contract.get('invocation', {})
        binary = invocation.get('executable')
        required = invocation.get('required_flags')
        if not binary or not isinstance(required, dict):
            raise ValueError('command transport requires executable and explicit required_flags')
        words = command_words(env.get('BENCHMARK_COMMAND', ''))
        if words.count(binary) != 1:
            raise ValueError('reviewed benchmark executable must occur exactly once')
        args = []
        for word in words[words.index(binary) + 1:]:
            if word and all(c in ';&|<>()' for c in word):
                break
            args.append(word)
        for flag, expected in required.items():
            if not flag.startswith('-') or not isinstance(expected, str):
                raise ValueError('invalid required flag declaration')
            actual = []
            for i, arg in enumerate(args):
                if arg.lstrip('-') == flag.lstrip('-'):
                    actual.append(args[i + 1] if i + 1 < len(args) else None)
                elif arg.lstrip('-').startswith(flag.lstrip('-') + '='):
                    actual.append(arg.split('=', 1)[1])
            if actual != [expected]:
                raise ValueError('missing, conflicting or duplicate benchmark argument: ' + flag)
    elif env.get('BENCHMARK_TRANSPORT') != 'http':
        raise ValueError('unsupported benchmark transport')
    fingerprint = effective(root, env)
    if seal and contract.get('effective_sha256') != fingerprint:
        raise ValueError('effective benchmark settings changed; review and reseal the contract')
    return fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--seal', action='store_true', help='after operator review, bind the exact effective settings; does not launch a benchmark')
    args = parser.parse_args()
    root = args.root.resolve()
    path = root / os.environ.get('BENCHMARK_CONTRACT_FILE', 'config/benchmark-contract.json')
    try:
        value = json.loads(path.read_text())
        fingerprint = check(root, value, os.environ, seal=not args.seal)
        if args.seal:
            value['effective_sha256'] = fingerprint
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        print('benchmark contract ' + ('sealed' if args.seal else 'verified') + ': ' + fingerprint[:12])
    except (ValueError, KeyError, OSError) as exc:
        sys.exit('benchmark contract rejected: ' + str(exc))


if __name__ == '__main__':
    main()
