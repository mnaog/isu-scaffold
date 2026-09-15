#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
language=${1:-}
application_path=${2:-}
[[ "${CONFIRM_DRAFT:-false}" == true ]] || {
  echo "review .local/draft and rerun with CONFIRM_DRAFT=true" >&2
  exit 2
}
test -n "${language}" || { echo "LANGUAGE=<name> is required" >&2; exit 2; }
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi

"${script_dir}/configure-apply.sh"
"${script_dir}/discover.sh"
"${script_dir}/sync-check.sh"
"${script_dir}/import.sh"
"${script_dir}/set-application-language.sh" "${language}" "${application_path}"

echo "kickoff apply complete"
echo "review the import and config/application.env, create the initial commit, run make kickoff-code-ready if the code worktree does not exist yet, then run: make phase1-check"
