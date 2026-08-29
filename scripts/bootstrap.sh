#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUCON_INTERNAL_OPERATION_LOCK_HELD:-false}" != true ]]; then
  exec "${script_dir}/with-operation-lock.sh" "$0" "$@"
fi

"${script_dir}/ansible-install.sh"
"${script_dir}/discover.sh"
"${script_dir}/bootstrap-ssh.sh"
"${script_dir}/run-ansible-playbook.sh" bootstrap.yml

echo "bootstrap complete; adapt the application import/deploy adapters, then run make phase1-check"
