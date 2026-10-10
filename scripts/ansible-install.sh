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
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi
if [[ "${1:-}" == --all ]]; then
  for profile in legacy modern; do
    ANSIBLE_PROFILE=$profile ANSIBLE_VENV_DIR="${repo_dir}/.local/ansible-venv-$profile" bash "$0"
  done
  exit 0
fi
python_bin=${PYTHON_BIN:-python3}
case "${ANSIBLE_PROFILE:-legacy}" in legacy|modern) ;; *) echo "invalid ANSIBLE_PROFILE" >&2; exit 2 ;; esac
venv_dir=${ANSIBLE_VENV_DIR:-${repo_dir}/.local/ansible-venv-${ANSIBLE_PROFILE:-legacy}}
requirements_file=${repo_dir}/ansible/requirements-${ANSIBLE_PROFILE:-legacy}.txt

command -v "${python_bin}" >/dev/null
"${python_bin}" -c 'import sys; assert (3, 12) <= sys.version_info[:2] <= (3, 13), "Use controller Python 3.12 or 3.13 for both profiles"'
test -f "${requirements_file}"

requirements_hash=$("${python_bin}" -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "${requirements_file}")
requirements_stamp=${venv_dir}/.requirements.sha256

if [[ ! -x "${venv_dir}/bin/python" ]]; then
  "${python_bin}" -m venv "${venv_dir}"
fi

"${venv_dir}/bin/python" -c 'import sys; assert (3, 12) <= sys.version_info[:2] <= (3, 13), "Existing controller venv has an unsupported Python; select a fresh ANSIBLE_VENV_DIR"'

if [[ ! -x "${venv_dir}/bin/ansible-playbook" ]] ||
  [[ ! -f "${requirements_stamp}" ]] ||
  [[ "$(<"${requirements_stamp}")" != "${requirements_hash}" ]]; then
  "${venv_dir}/bin/python" -m pip install --disable-pip-version-check --requirement "${requirements_file}"
  printf '%s\n' "${requirements_hash}" >"${requirements_stamp}"
fi
"${venv_dir}/bin/ansible-playbook" --version | sed -n '1p'
