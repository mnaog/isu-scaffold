#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
[[ "${CONFIRM_DRAFT:-false}" == true ]] || {
  echo "read .local/draft/review.md and rerun with CONFIRM_DRAFT=true" >&2
  exit 2
}
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${repo_dir}/.local/operation.lock" -- "$0" "$@"
fi

# shellcheck disable=SC1091
source "${repo_dir}/config/application.env"

# The draft may have been edited since kickoff; review it again before applying.
python3 "${script_dir}/review-draft.py"
"${script_dir}/configure-apply.sh"
"${script_dir}/discover.sh"
# The applied draft may set the session source for the measurement log; rewrite only that drop-in.
"${script_dir}/run-ansible-playbook.sh" bootstrap.yml --tags observability_nginx_log
"${script_dir}/sync-check.sh"
"${script_dir}/import.sh"

echo "kickoff apply complete"
echo "review the import, commit the initial state, then run: make deploy && make phase1-check"
