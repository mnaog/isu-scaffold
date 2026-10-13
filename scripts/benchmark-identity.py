#!/usr/bin/env python3
"""One id for what a benchmark measures: the deployed files and the measurement settings.

The deployed files are those check-deployed-commit.sh compares (sync.json items, the builder
Dockerfiles, sync.json itself) as committed at HEAD, plus the tracked measurement settings
(.isuscope/, config/isuscope-policy.json, config/benchmark.env), the generated
.isuscope/config.toml, and whether the servers run the distributed build or a deploy.
benchmark-prepare.sh refuses to measure the same id twice in a row unless BENCH_REPEAT says
why: in practice second a sed that matched nothing left the configuration as it was, and a
full run measured it again.
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
    tree = subprocess.run(['git', 'ls-tree', '-r', 'HEAD', '--', *sorted(paths)], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout
    digest = hashlib.sha256(tree.encode())
    generated = ROOT / '.isuscope/config.toml'
    if generated.exists():
        digest.update(generated.read_bytes())
    digest.update(b'deployed' if (ROOT / '.local/current-deploy-commit').exists() else b'distributed')
    print(digest.hexdigest())


if __name__ == '__main__':
    main()
