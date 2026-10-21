#!/usr/bin/env bash
set -euo pipefail

# Reads main's isuscope runs from any worktree: `scripts/runs.sh brief latest`.
# The settings (.isuscope/config.toml, written by discover, not in Git) and the runs are only in
# the root repository, where main benchmarks. ISUSCOPE_PROJECT points isuscope there; isuscope
# accepts only reading commands for a project other than the current directory's.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
common=$(git -C "${script_dir}" rev-parse --path-format=absolute --git-common-dir)
ISUSCOPE_PROJECT=$(dirname -- "${common}") exec isuscope "$@"
