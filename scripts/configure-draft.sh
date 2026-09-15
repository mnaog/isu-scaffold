#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi
inventory=${ANSIBLE_INVENTORY:-${repo_dir}/.local/ansible-inventory.json}
inspection_dir=${INSPECTION_OUTPUT_DIR:-${repo_dir}/.local/inspection}
output_dir=${CONFIGURE_DRAFT_DIR:-${repo_dir}/.local/draft}

command -v python3 >/dev/null
test -f "${inventory}" || { echo "run make discover first" >&2; exit 1; }
test -d "${inspection_dir}" || { echo "run make inspect first" >&2; exit 1; }
exec python3 "${script_dir}/configure-draft.py" "${inventory}" "${inspection_dir}" "${output_dir}"
