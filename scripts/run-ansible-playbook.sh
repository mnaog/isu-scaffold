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
# Per-task wall time (ansible/callback_plugins/task_timing.py) for tuning the kickoff.
export ANSIBLE_TASK_TIMING_FILE=${ANSIBLE_TASK_TIMING_FILE:-${repo_dir}/.local/ansible-task-timing.tsv}
# kickoff hands bootstrap and inspect one fact cache for that run only, so inspect reads the
# OS, CPU and memory bootstrap already gathered. It never outlives the run: a cache shared
# across runs could describe a previous environment's nodes of the same name.
if [[ -n "${ANSIBLE_FACT_CACHE_DIR:-}" ]]; then
  export ANSIBLE_CACHE_PLUGIN=jsonfile
  export ANSIBLE_CACHE_PLUGIN_CONNECTION=${ANSIBLE_FACT_CACHE_DIR}
  export ANSIBLE_GATHERING=smart
fi
# BENCHMARK_NODE_ACCESS=none（既定）なら、どのplaybookもベンチの台へ接続しない（本番では運営の台）。
limit=()
if [[ "$(jq -r '.all.vars.isucon_benchmark_node_access // "none"' "${inventory_path}")" != ssh ]]; then
  limit=(--limit 'all:!benchmark')
fi
exec "${venv_dir}/bin/ansible-playbook" \
  --inventory "${inventory_path}" \
  ${limit[@]+"${limit[@]}"} \
  "${repo_dir}/ansible/playbooks/${playbook_name}" \
  "$@"
