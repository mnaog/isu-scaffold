#!/usr/bin/env python3
"""Does the working tree still hold exactly what the full import took from the servers?

import.sh records each sync item's digest in .local/imported-state.json. Before the first
deploy the servers run that content as distributed, so when every item of config/sync.json
still has its imported digest here, a benchmark measures the commit at HEAD (except the
binary, which is the distributed build of the same source). make first-bench uses this to
take the baseline without waiting for the local build and deploy.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    state_path = ROOT / '.local/imported-state.json'
    manifest = Path(os.environ.get('SYNC_MANIFEST') or ROOT / 'config/sync.json')
    if not state_path.exists():
        sys.exit('no imported state (.local/imported-state.json): run kickoff-apply, or deploy first')
    state = json.loads(state_path.read_text())['items']
    items = {item['name']: item for item in json.loads(manifest.read_text())['items']}
    if set(items) != set(state):
        sys.exit(f'config/sync.json items changed since the import: {sorted(set(items) ^ set(state))}')
    for name, entry in sorted(state.items()):
        if items[name]['local'] != entry['local']:
            sys.exit(f'{name}: local path changed since the import')
        command = [sys.executable, str(ROOT / 'scripts/tree-digest.py'), str(ROOT / entry['local'])]
        for pattern in entry.get('exclude', []):
            command += ['--exclude', pattern]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode or result.stdout.strip() != entry['digest']:
            sys.exit(f'{name} ({entry["local"]}) differs from what the servers run as distributed')
    print(f'HEAD matches the imported state of {len(state)} items')


if __name__ == '__main__':
    main()
