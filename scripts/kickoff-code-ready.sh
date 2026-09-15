#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
"${script_dir}/create-phase1-worktree.sh"
echo "start code and schema review in the phase1 worktree while main runs make kickoff-draft"
