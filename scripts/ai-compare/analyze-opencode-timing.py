#!/usr/bin/env python3
"""Summarize observable OpenCode intervals without exporting reasoning text."""
import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--destination', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
local = a.destination.resolve() / '.local'
run = json.loads((local / 'run.json').read_text())
events = [json.loads(line) for line in (local / 'opencode.stdout.jsonl').read_text().splitlines()]
parts = [e['part'] for e in events if e['type'] == 'tool_use']
spans = sorted((t['state']['time']['start']/1000, t['state']['time']['end']/1000) for t in parts)
merged = []
for start, end in spans:
    if merged and start <= merged[-1][1]:
        merged[-1][1] = max(end, merged[-1][1])
    else:
        merged.append([start, end])
union = sum(end-start for start,end in merged)
steps = [e for e in events if e['type'] == 'step_finish']
tokens = [e['part']['tokens'] for e in steps]
heavy = []
for index, step in enumerate(steps):
    if step['part']['tokens']['reasoning'] <= 1000:
        continue
    start = steps[index-1]['timestamp']/1000 if index else run['started_at']
    end = step['timestamp']/1000
    heavy.append({'step': index+1, 'from_seconds': start-run['started_at'],
                  'to_seconds': end-run['started_at'], 'wall_seconds': end-start,
                  'reported_reasoning_tokens': step['part']['tokens']['reasoning']})
report = {'trial':run['trial'], 'elapsed_seconds':run['elapsed_seconds'],
          'tool_interval_union_seconds':union,
          'tool_interval_sum_seconds':sum(end-start for start,end in spans),
          'outside_recorded_tool_intervals_seconds':run['elapsed_seconds']-union,
          'response_steps':len(steps),
          'first_edit_seconds':min(t['state']['time']['start']/1000-run['started_at'] for t in parts if t['tool']=='edit'),
          'reported_reasoning_tokens':sum(t['reasoning'] for t in tokens),
          'reported_output_tokens':sum(t['output'] for t in tokens),
          'reported_uncached_input_tokens':sum(t['input'] for t in tokens),
          'reported_cached_input_tokens':sum(t['cache']['read'] for t in tokens),
          'last_response_input_tokens':tokens[-1]['input']+tokens[-1]['cache']['read'],
          'large_reasoning_steps':heavy,
          'note':'Step intervals include tool and harness activity. Outside tool spans includes model, network, hooks, CLI and uninstrumented overhead; it is not measured inference time.'}
a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(a.output)
