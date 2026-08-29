#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUCON_INTERNAL_OPERATION_LOCK_HELD:-false}" != true ]]; then
  exec "${script_dir}/with-operation-lock.sh" "$0" "$@"
fi
python_bin=${PYTHON_BIN:-python3}
venv_dir=${ANSIBLE_VENV_DIR:-${repo_dir}/.local/ansible-venv}
requirements_file=${repo_dir}/ansible/requirements.txt

command -v "${python_bin}" >/dev/null
"${python_bin}" -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12 or newer is required"'
test -f "${requirements_file}"

if [[ ! -x "${venv_dir}/bin/python" ]]; then
  "${python_bin}" -m venv "${venv_dir}"
fi

"${venv_dir}/bin/python" -m pip install --disable-pip-version-check --requirement "${requirements_file}"
"${venv_dir}/bin/ansible-playbook" --version | sed -n '1p'
