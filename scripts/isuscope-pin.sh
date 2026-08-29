#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <run-id>" >&2
  exit 2
fi

run_id=$1
if [[ ! ${run_id} =~ ^[0-9A-Za-z-]+$ ]]; then
  echo "invalid run id: ${run_id}" >&2
  exit 2
fi

repo_root=$(git rev-parse --show-toplevel)
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ "${ISUCON_INTERNAL_OPERATION_LOCK_HELD:-false}" != true ]]; then
  exec "${script_dir}/with-operation-lock.sh" "$0" "$@"
fi
run_relative="isuscope-data/runs/${run_id}"
run_dir="${repo_root}/${run_relative}"

if [[ ! -f "${run_dir}/run.json" ]]; then
  echo "run not found: ${run_relative}" >&2
  exit 1
fi

git -C "${repo_root}" add -- "${run_relative}/run.json"

for path in source tooling structured.json.zst; do
  if [[ -e "${run_dir}/${path}" ]]; then
    git -C "${repo_root}" add -- "${run_relative}/${path}"
  fi
done

if [[ ! -d "${run_dir}/logs" ]]; then
  echo "logs not found: ${run_relative}/logs" >&2
  exit 1
fi

git -C "${repo_root}" add -f -- "${run_relative}/logs"

echo "staged isuscope run including raw logs: ${run_id}"
echo "review with: git diff --cached --stat"
