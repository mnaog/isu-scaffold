#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ -z "${ANSIBLE_PROFILE:-}" && -f "${repo_dir}/.local/ansible-runtime.env" ]]; then
  source "${repo_dir}/.local/ansible-runtime.env"
fi
if [[ -f "${repo_dir}/config/ansible-controller.env" ]]; then
  source "${repo_dir}/config/ansible-controller.env"
fi
case "${ANSIBLE_PROFILE:-legacy}" in legacy|modern) ;; *) echo "invalid ANSIBLE_PROFILE" >&2; exit 2 ;; esac
venv_dir=${ANSIBLE_VENV_DIR:-${repo_dir}/.local/ansible-venv-${ANSIBLE_PROFILE:-legacy}}
inventory_path=${ANSIBLE_INVENTORY:-${repo_dir}/.local/ansible-inventory.json}
playbook_name=${1:-}

test -n "${playbook_name}" || { echo "usage: $0 PLAYBOOK [arguments ...]" >&2; exit 2; }
shift
test -x "${venv_dir}/bin/ansible-playbook" || { echo "run ./scripts/ansible-install.sh first" >&2; exit 1; }
test -f "${inventory_path}" || { echo "run make discover first" >&2; exit 1; }
test -f "${repo_dir}/ansible/playbooks/${playbook_name}"

export ANSIBLE_PYTHON_INTERPRETER=${ANSIBLE_PYTHON_INTERPRETER:-/usr/bin/python3}
export ANSIBLE_CONFIG=${repo_dir}/ansible/ansible.cfg
exec "${venv_dir}/bin/ansible-playbook" \
  --inventory "${inventory_path}" \
  "${repo_dir}/ansible/playbooks/${playbook_name}" \
  "$@"
