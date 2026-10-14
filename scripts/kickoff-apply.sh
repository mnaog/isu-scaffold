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
# shellcheck disable=SC1091
source "${script_dir}/stage-lib.sh"

# The draft may have been edited since kickoff; review it again before applying.
stage review-draft python3 "${script_dir}/review-draft.py"
stage configure-apply "${script_dir}/configure-apply.sh"
# Start the Rust implementation where the draft found another language running.
stage switch-language "${script_dir}/switch-language-service.sh"
stage discover "${script_dir}/discover.sh"
# The applied draft may set the session source for the measurement log and the fingerprint
# paths (the synced files); rewrite only that drop-in and the isuscope node helpers.
stage nginx-log "${script_dir}/run-ansible-playbook.sh" bootstrap.yml --tags observability_nginx_log,observability_helpers
stage sync-check "${script_dir}/sync-check.sh"
stage import "${script_dir}/import.sh"

echo "kickoff apply complete"
echo "review the import, commit the initial state, then run: make deploy && make bench-check"
