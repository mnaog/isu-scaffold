#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi
draft_dir=${CONFIGURE_DRAFT_DIR:-${repo_dir}/.local/draft}

[[ "${CONFIRM_DRAFT:-false}" == true ]] || {
  echo "review .local/draft, then run CONFIRM_DRAFT=true make configure-apply" >&2
  exit 2
}
for file in node-overrides.json sync.json ansible-vars.json isuscope.json; do
  test -f "${draft_dir}/${file}" || { echo "missing draft: ${draft_dir}/${file}" >&2; exit 1; }
  jq -e . "${draft_dir}/${file}" >/dev/null
done

backup_dir=${repo_dir}/.local/draft-backup/$(date +%Y%m%d-%H%M%S)
mkdir -p "${backup_dir}" "${repo_dir}/.local"
cp -a "${repo_dir}/config/sync.json" "${backup_dir}/sync.json"
cp -a "${repo_dir}/config/ansible-vars.json" "${backup_dir}/ansible-vars.json"
install -m 0644 "${draft_dir}/sync.json" "${repo_dir}/config/sync.json"
install -m 0644 "${draft_dir}/ansible-vars.json" "${repo_dir}/config/ansible-vars.json"
install -m 0600 "${draft_dir}/node-overrides.json" "${repo_dir}/.local/node-overrides.json"
install -m 0600 "${draft_dir}/isuscope.json" "${repo_dir}/.local/isuscope-overrides.json"

echo "draft applied; tracked config changes require review"
echo "backup: ${backup_dir}"
echo "next: make discover && make sync-check"
