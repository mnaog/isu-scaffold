#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUCON_INTERNAL_OPERATION_LOCK_HELD:-false}" != true ]]; then
  exec "${script_dir}/with-operation-lock.sh" "$0" "$@"
fi
output_dir=${INSPECTION_OUTPUT_DIR:-${repo_dir}/.local/inspection}

mkdir -p "${output_dir}"
"${script_dir}/run-ansible-playbook.sh" inspect.yml \
  --extra-vars "inspection_output_dir=${output_dir}"
printf 'inspection reports: %s\n' "${output_dir}"
