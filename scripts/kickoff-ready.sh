#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
"${script_dir}/create-phase1-worktree.sh"

echo "the code-reading lane can start in the worktree; continuing main setup checks"
"${script_dir}/phase1-check.sh"

echo "kickoff ready; review the checks, then run the initial survey explicitly"
