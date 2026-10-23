#!/usr/bin/env python3
"""Write the CPU profile a benchmark run took from the Go app as the app's PGO profile.

isuscope's `go-profile` collector keeps, per application node, what the app's profile endpoint
(`profile.go`) returned at the end of the run: the CPU profile of the run, among others. `go build`
uses `default.pgo` in the main package's directory by itself (`-pgo=auto`), so committing that file
is the whole change; `make build` rebuilds because the source tree changed.

    make pgo RUN=<run ID|short ID|latest>          # every node that took a profile, merged
    make pgo RUN=<run> NODE=app3                    # one node
    python3 scripts/pgo.py <run> --output webapp/go/default.pgo

Take the profile from a run of the configuration the change builds on, under the full load: PGO
optimizes the paths that were hot then. It is a change like any other: submit it and let main measure.
"""
import argparse
import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

REPO = Path(__file__).resolve().parent.parent
COLLECTOR = 'go-profile-stop'


def root():
    """The root repository: the runs are only there, whatever worktree this runs in."""
    common = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', '--path-format=absolute',
                                      '--git-common-dir'], text=True).strip()
    return Path(common).parent


def runs_dir(project):
    data_dir = 'isuscope-data'
    config = project / '.isuscope/config.toml'
    if config.is_file():
        data_dir = tomllib.loads(config.read_text()).get('data_dir', data_dir)
    return (project / data_dir / 'runs').resolve()


def find_run(directory, requested):
    runs = sorted(path for path in directory.iterdir() if path.is_dir() and not path.name.startswith('.'))
    if requested == 'latest':
        found = [path for path in runs if list((path / 'logs').glob(f'{COLLECTOR}-*-after-stdout.zst'))]
        if not found:
            raise SystemExit(f'pgo: no run in {directory} has a Go profile ({COLLECTOR})')
        return found[-1]
    found = [path for path in runs if path.name == requested or path.name.endswith(requested)]
    if len(found) != 1:
        raise SystemExit(f'pgo: {len(found)} runs in {directory} match {requested!r}')
    return found[0]


def decompress(path):
    if shutil.which('zstd'):
        return subprocess.run(['zstd', '-dcq', str(path)], check=True, capture_output=True).stdout
    try:
        from compression import zstd  # Python 3.14
    except ImportError:
        raise SystemExit('pgo: install zstd to read the run logs')
    return zstd.decompress(path.read_bytes())


def cpu_profile(text):
    """The gzipped CPU profile in one `go-profile-stop` output, or None."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith('isuscope-go-profile 1 '):
        return None
    for line in lines[1:]:
        parts = line.split(' ', 2)
        if len(parts) == 3 and parts[:2] == ['profile', 'cpu']:
            return base64.b64decode(parts[2])
    return None


def profiles(run, node=None):
    found = {}
    for path in sorted((run / 'logs').glob(f'{COLLECTOR}-*-after-stdout.zst')):
        name = path.name[len(COLLECTOR) + 1:-len('-after-stdout.zst')]
        if node and name != node:
            continue
        profile = cpu_profile(decompress(path).decode(errors='replace'))
        if profile:
            found[name] = profile
    return found


def main_package():
    for candidate in sorted(REPO.glob('webapp/*/go.mod')):
        return candidate.parent
    raise SystemExit('pgo: no webapp/*/go.mod; pass --output')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('run', help='run ID, its short ID, or latest (the newest run with a Go profile)')
    parser.add_argument('--node', help='take the profile of this node only')
    parser.add_argument('--output', type=Path, help='default: default.pgo in the Go app (webapp/*/go.mod)')
    parser.add_argument('--runs-dir', type=Path, help="isuscope's runs directory (default: the root repository's)")
    args = parser.parse_args()
    run = find_run(args.runs_dir or runs_dir(root()), args.run)
    found = profiles(run, args.node)
    if not found:
        raise SystemExit(f'pgo: run {run.name[-8:]} has no Go CPU profile'
                         f'{" from " + args.node if args.node else ""} (is the go-profile collector on, and profile.go deployed?)')
    output = args.output or main_package() / 'default.pgo'
    if len(found) == 1:
        data = next(iter(found.values()))
    else:
        go = shutil.which('go') or str(Path.home() / '.local/go/bin/go')
        with tempfile.TemporaryDirectory() as work:
            paths = []
            for name, profile in found.items():
                path = Path(work, f'{name}.pprof')
                path.write_bytes(profile)
                paths.append(str(path))
            data = subprocess.run([go, 'tool', 'pprof', '-proto', *paths], check=True, capture_output=True,
                                  env={**os.environ, 'GOTOOLCHAIN': 'local'}).stdout
    output.write_bytes(data)
    print(f'pgo: wrote {output.relative_to(REPO) if output.is_relative_to(REPO) else output} '
          f'({len(data)} bytes) from run {run.name[-8:]} ({", ".join(sorted(found))}); commit it and submit the change')


if __name__ == '__main__':
    main()
