#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi

# shellcheck disable=SC1091
source "${script_dir}/stage-lib.sh"
stage bootstrap/discover "${script_dir}/discover.sh"
stage bootstrap/ssh "${script_dir}/bootstrap-ssh.sh"
stage bootstrap/ansible-runtime python3 "${script_dir}/ansible-runtime.py"
stage bootstrap/ansible-install "${script_dir}/ansible-install.sh"
stage bootstrap/playbook "${script_dir}/run-ansible-playbook.sh" bootstrap.yml

echo "bootstrap complete; adapt the application import/deploy adapters, then run make phase1-check"
