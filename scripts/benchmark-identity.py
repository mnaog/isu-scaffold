#!/usr/bin/env python3
"""isuscope's identity_command: what a benchmark measures, as a JSON object of fields.

- deployed_files: the files check-deployed-commit.sh compares (sync.json items, the builder
  Dockerfiles, sync.json itself) as committed at HEAD
- benchmark_env: config/benchmark.env as committed (which benchmark runs, against what, how
  long). isuscope hashes its own settings (config.toml with .isuscope/policy.toml, routes.toml,
  the adapter scripts) into the run's observation, so they are not repeated here.
- build: whether the servers run the distributed build or a deploy of ours
- waits: the per-API waits (ISUCON_WAITS, scripts/waits.py) as committed, when there are any, so
  each run records the table it ran with and a change shows as `changed identity.waits`

The deployed commit itself is left out: a docs-only commit followed by a deploy that sends
nothing must still count as the same configuration.

isuscope compares the fields with the last run whose benchmark started, together with the node
fingerprints and its observation, and refuses an identical run unless
`isuscope run --repeat "<reason>"`.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

ROOT = Path(os.environ.get('REPO_DIR') or Path(__file__).resolve().parent.parent)


def committed_waits():
    """The ISUCON_WAITS line as committed under config/, normalized to one line."""
    found = subprocess.run(['git', 'grep', '-h', '-e', '^ISUCON_WAITS=', '-e', '^export ISUCON_WAITS=', 'HEAD', '--', 'config'],
                           cwd=ROOT, capture_output=True, text=True).stdout
    tables = []
    for line in found.splitlines():
        value = re.sub(r'^(HEAD:)?\s*(export\s+)?ISUCON_WAITS=', '', line)
        try:
            value = ' '.join(shlex.split(value))
        except ValueError:
            value = value.strip()
        value = ';'.join(' '.join(rule.split()) for rule in value.split(';') if rule.strip())
        if value and value not in tables:
            tables.append(value)
    return ' | '.join(tables)


def main():
    manifest = Path(os.environ.get('SYNC_MANIFEST') or ROOT / 'config/sync.json')
    sync = json.loads(manifest.read_text())
    try:
        manifest_relative = manifest.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        manifest_relative = str(manifest)
    paths = {manifest_relative}
    paths |= {item['local'] for item in sync.get('items', [])}
    paths |= {build['dockerfile'] for build in sync.get('local_builds', []) if build.get('dockerfile')}

    def tree(selected):
        listing = subprocess.run(['git', 'ls-tree', '-r', 'HEAD', '--', *sorted(selected)], cwd=ROOT,
                                 capture_output=True, text=True, check=True).stdout
        return hashlib.sha256(listing.encode()).hexdigest()[:16]

    deploy_record = ROOT / '.local/current-deploy-commit'
    identity = {
        'deployed_files': tree(paths),
        'benchmark_env': tree({'config/benchmark.env'}),
        'build': 'deployed' if deploy_record.exists() else 'distributed',
    }
    waits = committed_waits()
    if waits:
        identity['waits'] = waits
    print(json.dumps(identity, sort_keys=True, ensure_ascii=False))


if __name__ == '__main__':
    main()
