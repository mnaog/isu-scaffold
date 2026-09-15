#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
venv_dir=${ANSIBLE_VENV_DIR:-${repo_dir}/.local/ansible-venv}
inventory_path=${ANSIBLE_INVENTORY:-${repo_dir}/.local/ansible-inventory.json}

cd "${repo_dir}"

command -v isuscope >/dev/null
test -x "${venv_dir}/bin/ansible"
test -f "${inventory_path}"
test -z "$(git ls-files -- .local)" || { echo '.local contains tracked files' >&2; exit 1; }
git diff --check

while IFS= read -r shell_file; do
  bash -n "${shell_file}"
done < <(find scripts .isuscope -type f -name '*.sh' -print | sort)

export ANSIBLE_CONFIG=${repo_dir}/ansible/ansible.cfg
"${venv_dir}/bin/ansible-playbook" --inventory "${inventory_path}" ansible/playbooks/bootstrap.yml --syntax-check
"${venv_dir}/bin/ansible-playbook" --inventory "${inventory_path}" ansible/playbooks/verify.yml --syntax-check
"${venv_dir}/bin/ansible-playbook" --inventory "${inventory_path}" ansible/playbooks/inspect.yml --syntax-check
"${script_dir}/run-ansible-playbook.sh" verify.yml
"${script_dir}/sync-check.sh"
"${repo_dir}/.isuscope/benchmark.sh" --check
"${repo_dir}/.isuscope/benchmark.sh" --probe

isuscope list --limit 1 >/dev/null
if [[ "${PHASE1_SKIP_ISUSCOPE_DOCTOR:-false}" != true ]]; then
  isuscope doctor
fi

"${script_dir}/collector-smoke.sh"

echo "phase1 pre-benchmark checks passed"
