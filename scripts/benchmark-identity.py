#!/usr/bin/env python3
"""isuscope's identity_command: what a benchmark measures, as a JSON object of fields.

- deployed_files: the files check-deployed-commit.sh compares (sync.json items, the builder
  Dockerfiles, sync.json itself) as committed at HEAD
- measurement: the tracked measurement settings (.isuscope/, config/isuscope-policy.json,
  config/benchmark.env) and the generated .isuscope/config.toml
- build: whether the servers run the distributed build or a deploy of ours

The deployed commit itself is left out: a docs-only commit followed by a deploy that sends
nothing must still count as the same configuration.

isuscope compares the fields with the last run whose benchmark started, together with the node
fingerprints, and refuses an identical run unless `isuscope run --repeat "<reason>"`: in
practice second a sed that matched nothing left the configuration as it was, and a full run
measured it again.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(os.environ.get('REPO_DIR') or Path(__file__).resolve().parent.parent)


def main():
    manifest = Path(os.environ.get('SYNC_MANIFEST') or ROOT / 'config/sync.json')
    sync = json.loads(manifest.read_text())
    try:
        manifest_relative = manifest.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        manifest_relative = str(manifest)
    paths = {manifest_relative, '.isuscope', 'config/isuscope-policy.json', 'config/benchmark.env'}
    paths |= {item['local'] for item in sync.get('items', [])}
    paths |= {build['dockerfile'] for build in sync.get('local_builds', []) if build.get('dockerfile')}
    measurement_paths = {'.isuscope', 'config/isuscope-policy.json', 'config/benchmark.env'}

    def tree(selected):
        listing = subprocess.run(['git', 'ls-tree', '-r', 'HEAD', '--', *sorted(selected)], cwd=ROOT,
                                 capture_output=True, text=True, check=True).stdout
        return hashlib.sha256(listing.encode())

    measurement = tree(measurement_paths)
    generated = ROOT / '.isuscope/config.toml'
    if generated.exists():
        measurement.update(generated.read_bytes())
    deploy_record = ROOT / '.local/current-deploy-commit'
    print(json.dumps({
        'deployed_files': tree(paths - measurement_paths).hexdigest()[:16],
        'measurement': measurement.hexdigest()[:16],
        'build': 'deployed' if deploy_record.exists() else 'distributed',
    }, sort_keys=True))


if __name__ == '__main__':
    main()
