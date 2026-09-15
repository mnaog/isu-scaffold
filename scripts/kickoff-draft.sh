#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi

"${script_dir}/bootstrap.sh"
"${script_dir}/inspect-environment.sh"
"${script_dir}/configure-draft.sh"
echo "review .local/draft, then run: CONFIRM_DRAFT=true make kickoff-apply LANGUAGE=<name>"
