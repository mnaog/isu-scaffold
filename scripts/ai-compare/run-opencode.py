#!/usr/bin/env python3
"""Run one fresh OpenCode/Lithos Flash trial with recorded settings and events."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--destination', type=Path, required=True)
p.add_argument('--key-file', type=Path, default=ROOT / '.local/lithos-ultra/api-key')
a = p.parse_args()
dest = a.destination.resolve()
config = ROOT / 'config/ai-compare/opencode.json'
env = dict(os.environ, OPENCODE_CONFIG=str(config))
if not env.get('LITHOS_API_KEY'):
    env['LITHOS_API_KEY'] = a.key_file.read_text().strip()
version = subprocess.check_output(['opencode', '--version'], text=True).strip()
model = 'lithosai/deepseek-ai/DeepSeek-V4.1-Flash'
args = [sys.executable, str(ROOT / 'scripts/ai-compare/manage.py'), 'run',
        '--destination', str(dest), '--agent', 'opencode', '--model', model,
        '--endpoint', 'https://api.lithosai.cloud/v1', '--reasoning', 'max',
        '--harness', f'OpenCode {version}; build agent; agent-history enabled; LSP/formatter disabled; config permissions',
        '--settings', str(config)]
for path in [Path.home() / '.config/opencode/opencode.jsonc',
             Path.home() / '.config/opencode/plugins/agent-history.js']:
    if path.exists():
        args.extend(['--settings', str(path)])
args.extend(['--', 'opencode', 'run', '--dir', str(dest), '--model', model, '--agent', 'build', '--format', 'json',
             '--title', 'task-' + dest.name, (dest / 'TASK.txt').read_text()])
# Exclusive creation protects the evidence of an earlier attempt.
with (dest / '.local/opencode.stdout.jsonl').open('x') as out, \
     (dest / '.local/opencode.stderr.log').open('x') as err:
    result = subprocess.run(args, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
print(f'OpenCode exited with {result.returncode}; evidence: {dest / ".local"}')
sys.exit(result.returncode)
