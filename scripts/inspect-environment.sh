#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi
output_dir=${INSPECTION_OUTPUT_DIR:-${repo_dir}/.local/inspection}

mkdir -p "${output_dir}"
"${script_dir}/run-ansible-playbook.sh" inspect.yml \
  --extra-vars "inspection_output_dir=${output_dir}"
printf 'inspection reports: %s\n' "${output_dir}"
