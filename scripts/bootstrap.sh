#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi

# shellcheck disable=SC1091
source "${script_dir}/stage-lib.sh"
# kickoff sets these when it already discovered the nodes and reached all of them in this run.
if [[ "${BOOTSTRAP_SKIP_DISCOVER:-}" == 1 ]]; then
  echo "using the inventory discovered earlier in this run"
else
  stage bootstrap/discover "${script_dir}/discover.sh"
fi
if [[ "${BOOTSTRAP_SKIP_SSH:-}" == 1 ]]; then
  echo "SSH was established on every node earlier in this run"
else
  stage bootstrap/ssh "${script_dir}/bootstrap-ssh.sh"
fi
stage bootstrap/ansible-runtime python3 "${script_dir}/ansible-runtime.py"
stage bootstrap/ansible-install "${script_dir}/ansible-install.sh"
stage bootstrap/playbook "${script_dir}/run-ansible-playbook.sh" bootstrap.yml

echo "bootstrap complete; adapt the application import/deploy adapters, then run make phase1-check"
