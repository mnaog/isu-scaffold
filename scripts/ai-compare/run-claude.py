#!/usr/bin/env python3
"""Run the common task in a separate fresh Claude Code session."""
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
config = ROOT / 'config/ai-compare/claude.json'
spec = json.loads(config.read_text())
version = subprocess.check_output(['claude', '--version'], text=True).strip()
env = dict(os.environ)
for key in ('CODEX_THREAD_ID', 'SCAFFOLD_SESSION_ID', 'SCAFFOLD_PARENT_SESSION_ID', 'SCAFFOLD_TASK_ID'):
    env.pop(key, None)
args = [sys.executable, str(ROOT / 'scripts/ai-compare/manage.py'), 'run',
        '--destination', str(dest), '--agent', 'claude', '--model', spec['model'],
        '--endpoint', 'Anthropic first party via existing Claude subscription login',
        '--reasoning', spec['effortLevel'],
        '--harness', f'{version}; independent CLI session; existing agent-history hooks; no web/subagents/MCP/LSP/auto-memory; fast mode off; bypassPermissions',
        '--settings', str(config)]
for path in [Path.home() / '.claude/settings.json', Path.home() / '.claude/settings.local.json',
             Path.home() / '.agent-history/agent_history.py']:
    if path.exists():
        args.extend(['--settings', str(path)])
args.extend(['--', 'claude', '--model', spec['model'], '--effort', spec['effortLevel'],
             '--settings', str(config), '--permission-mode', 'bypassPermissions',
             '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
             '--disallowedTools', 'Agent,Task,WebSearch,WebFetch,Skill,LSP',
             '--output-format', 'stream-json', '--verbose', '-p', (dest / 'TASK.txt').read_text()])
with (dest / '.local/claude.stdout.jsonl').open('x') as out, \
     (dest / '.local/claude.stderr.log').open('x') as err:
    result = subprocess.run(args, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
print(f'Claude Code exited with {result.returncode}; evidence: {dest / ".local"}')
sys.exit(result.returncode)
