#!/usr/bin/env python3
"""Run the common task in a separate, fresh Codex CLI session."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--destination', type=Path, required=True)
a = p.parse_args()
dest = a.destination.resolve()
config = ROOT / 'config/ai-compare/codex.json'
spec = json.loads(config.read_text())
version = subprocess.check_output(['codex', '--version'], text=True).strip()
env = dict(os.environ)
# The child owns its own real thread ID. Do not associate its hook events with
# this operator's existing conversation or a previous worker task.
for key in ('CODEX_THREAD_ID', 'SCAFFOLD_SESSION_ID', 'SCAFFOLD_PARENT_SESSION_ID', 'SCAFFOLD_TASK_ID'):
    env.pop(key, None)
args = [sys.executable, str(ROOT / 'scripts/ai-compare/manage.py'), 'run',
        '--destination', str(dest), '--agent', 'codex', '--model', spec['model'],
        '--endpoint', 'OpenAI via existing ChatGPT login', '--reasoning', spec['reasoning'],
        '--harness', f'{version}; independent CLI session; existing agent-history hooks; no web/subagents/MCP; full local execution',
        '--settings', str(config)]
for path in [Path.home() / '.codex/config.toml', Path.home() / '.codex/hooks.json',
             Path.home() / '.codex/hooks/repo_conversation_log.py']:
    if path.exists():
        args.extend(['--settings', str(path)])
command = ['codex', '--no-daemon', '-a', spec['approval_policy'], 'exec', '-C', str(dest),
           '-m', spec['model'], '-s', spec['sandbox'], '--json',
           '-o', str(dest / '.local/codex-final.txt')]
for key, value in spec['overrides'].items():
    command.extend(['-c', key + '=' + json.dumps(value)])
command.append((dest / 'TASK.txt').read_text())
args.extend(['--', *command])
with (dest / '.local/codex.stdout.jsonl').open('x') as out, \
     (dest / '.local/codex.stderr.log').open('x') as err:
    result = subprocess.run(args, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
print(f'Codex exited with {result.returncode}; evidence: {dest / ".local"}')
sys.exit(result.returncode)
