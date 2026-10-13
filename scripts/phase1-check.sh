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

cd "${repo_dir}"

command -v isuscope >/dev/null
test -x "${venv_dir}/bin/ansible"
test -f "${inventory_path}"
test -z "$(git ls-files -- .local)" || { echo '.local contains tracked files' >&2; exit 1; }

python3 "${script_dir}/isuscope-policy.py" "${repo_dir}/.isuscope/config.toml" --check

export ANSIBLE_PYTHON_INTERPRETER=${ANSIBLE_PYTHON_INTERPRETER:-/usr/bin/python3}
export ANSIBLE_CONFIG=${repo_dir}/ansible/ansible.cfg
"${venv_dir}/bin/ansible-playbook" --inventory "${inventory_path}" ansible/playbooks/bootstrap.yml --syntax-check
"${venv_dir}/bin/ansible-playbook" --inventory "${inventory_path}" ansible/playbooks/verify.yml --syntax-check
"${venv_dir}/bin/ansible-playbook" --inventory "${inventory_path}" ansible/playbooks/inspect.yml --syntax-check
"${script_dir}/run-ansible-playbook.sh" verify.yml
"${script_dir}/sync-check.sh"
"${repo_dir}/.isuscope/benchmark.sh" --check
"${repo_dir}/.isuscope/benchmark.sh" --probe
# Without an initialize boundary every run mixes initialize into the load summaries. The
# [initialize] route in .isuscope/routes.toml works from the first run; markers need a sample.
python3 "${script_dir}/benchmark-markers.py" check ||
  echo "WARN: set the initialize route (python3 scripts/benchmark-markers.py suggest-route, then set-route)" >&2

isuscope list --limit 1 >/dev/null
if [[ "${PHASE1_SKIP_ISUSCOPE_DOCTOR:-false}" != true ]]; then
  isuscope doctor
fi

echo "phase1 pre-benchmark checks passed"
