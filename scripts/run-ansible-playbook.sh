#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
venv_dir=${ANSIBLE_VENV_DIR:-${repo_dir}/.local/ansible-venv}
inventory_path=${ANSIBLE_INVENTORY:-${repo_dir}/.local/ansible-inventory.json}
playbook_name=${1:-}

test -n "${playbook_name}" || { echo "usage: $0 PLAYBOOK [arguments ...]" >&2; exit 2; }
shift
test -x "${venv_dir}/bin/ansible-playbook" || { echo "run make ansible-install first" >&2; exit 1; }
test -f "${inventory_path}" || { echo "run make discover first" >&2; exit 1; }
test -f "${repo_dir}/ansible/playbooks/${playbook_name}"

export ANSIBLE_CONFIG=${repo_dir}/ansible/ansible.cfg
exec "${venv_dir}/bin/ansible-playbook" \
  --inventory "${inventory_path}" \
  "${repo_dir}/ansible/playbooks/${playbook_name}" \
  "$@"
