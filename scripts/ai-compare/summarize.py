#!/usr/bin/env python3
"""Export outcome metadata and observable tool counts, excluding reasoning text."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--destination', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
dest = a.destination.resolve()
local = dest / '.local'
run = json.loads((local / 'run.json').read_text())
manifest = json.loads((local / 'manifest.json').read_text())
agent = run['agent']
events = []
for line in (local / f'{agent}.stdout.jsonl').read_text().splitlines():
    try:
        events.append(json.loads(line))
    except json.JSONDecodeError:
        pass
counts = Counter()
sessions = set()
finish = []
usage = []
for event in events:
    if agent == 'opencode':
        if event.get('sessionID'):
            sessions.add(event['sessionID'])
        part = event.get('part', {})
        if event.get('type') == 'tool_use':
            counts[part['tool']] += 1
        if event.get('type') == 'step_finish':
            finish.append(part.get('reason'))
            usage.append(part.get('tokens', {}))
    elif agent == 'codex':
        if event.get('thread_id'):
            sessions.add(event['thread_id'])
        item = event.get('item', {})
        if event.get('type') == 'item.completed' and item.get('type') in ('command_execution', 'file_change', 'mcp_tool_call'):
            counts[item['type']] += 1
        if event.get('type') == 'turn.completed':
            usage.append(event.get('usage', {}))
    elif agent == 'claude':
        if event.get('session_id'):
            sessions.add(event['session_id'])
        if event.get('type') == 'assistant':
            for block in event.get('message', {}).get('content', []):
                if block.get('type') == 'tool_use':
                    counts[block['name']] += 1
        if event.get('type') == 'result':
            finish.append(event.get('subtype'))
            usage.append(event.get('usage', {}))
validations = [json.loads(line) for line in (local / 'validation.jsonl').read_text().splitlines()]
validations = [r for r in validations if run['started_at'] <= r['started_at'] <= run['ended_at']]
diff = subprocess.check_output(['git', '-C', str(dest), 'diff', '--numstat', manifest['task_commit'], 'HEAD'], text=True)
audit = json.loads((local / 'audit.json').read_text()) if (local / 'audit.json').exists() else None
report = {k: run[k] for k in ('trial','agent','model','endpoint','reasoning','harness','elapsed_seconds','exit_code','head','dirty')}
report.update(initial_commit=manifest['task_commit'], sessions=sorted(sessions),
              tool_counts=dict(counts), finish_reasons=finish, usage=usage,
              validation=[{k:r[k] for k in ('phase','exit_code','elapsed_seconds','queue_seconds')} for r in validations],
              validation_seconds=sum(r['elapsed_seconds'] for r in validations),
              diff_numstat=diff, audit=audit,
              evidence_directory=str(local),
              timing_note='CLI start to exit. Validation time excludes preparation and operator audit; residual is not model-only time.')
a.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(a.output)
