#!/usr/bin/env python3
"""Run a CLI that takes its prompt only as an argument: append stdin as the last argument.

The scouts hand the prompt over on stdin (runner.invoke). Kimi Code reads it only from
`-p <prompt>`, so config/scout.json runs `python3 scripts/scout/stdin_as_arg.py kimi ... -p`.
"""
import os
import shutil
import sys


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.exit('usage: stdin_as_arg.py <cli> [args...]')
    if shutil.which(argv[0]) is None:
        sys.exit(f'No such file: CLI {argv[0]}')
    prompt = sys.stdin.read()
    if not prompt.strip():
        sys.exit('empty prompt on stdin')
    os.execvp(argv[0], [*argv, prompt])


if __name__ == '__main__':
    main()
