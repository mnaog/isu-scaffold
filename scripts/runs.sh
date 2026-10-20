#!/usr/bin/env bash
set -euo pipefail

# Reads main's isuscope runs from any worktree: `scripts/runs.sh brief latest`.
# isuscope finds .isuscope/config.toml (written by discover, not in Git) and isuscope-data from the
# directory it runs in, and only the root repository, where main benchmarks, has both. This runs
# isuscope there, and only its reading commands, so improve and people in worktrees read the same
# runs main analyzed without changing directory. Recording (run, analyze, change decide) is main's.
# Usage: runs.sh <list|brief|query|sql|series|states|change list|change show> [ARGS...]
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
common=$(git -C "${script_dir}" rev-parse --path-format=absolute --git-common-dir)
root=$(dirname -- "${common}")

command=${1:-}
case "${command}" in
  list | brief | query | sql | series | states | help | --help | -h) ;;
  change)
    case "${2:-}" in
      list | show) ;;
      *) echo "runs.sh reads runs only: change list|show (change decide is main's)" >&2; exit 2 ;;
    esac
    ;;
  *)
    echo "runs.sh reads runs only: list, brief, query, sql, series, states, change list|show" >&2
    exit 2
    ;;
esac
test -f "${root}/.isuscope/config.toml" || {
  echo "${root}/.isuscope/config.toml is missing; main writes it with make kickoff (discover)" >&2
  exit 1
}
cd "${root}"
exec isuscope "$@"
