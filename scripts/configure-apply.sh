#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi
draft_dir=${CONFIGURE_DRAFT_DIR:-${repo_dir}/.local/draft}

[[ "${CONFIRM_DRAFT:-false}" == true ]] || {
  echo "review .local/draft, then run CONFIRM_DRAFT=true ./scripts/configure-apply.sh" >&2
  exit 2
}
for file in node-overrides.json sync.json ansible-vars.json isuscope.json; do
  test -f "${draft_dir}/${file}" || { echo "missing draft: ${draft_dir}/${file}" >&2; exit 1; }
  jq -e . "${draft_dir}/${file}" >/dev/null
done

# Use the same role file as discovery; otherwise reviewed roles are silently ignored.
environment_file=${ISUCON_ENV_FILE:-${repo_dir}/.local/environment.env}
if [[ -f "${environment_file}" ]]; then
  source "${environment_file}"
fi
overrides_file=${NODE_OVERRIDES_FILE:-.local/node-overrides.json}
case "${overrides_file}" in /*) ;; *) overrides_file=${repo_dir}/${overrides_file} ;; esac
python3 - "${repo_dir}" "${overrides_file}" <<'CHECK_PATH'
from pathlib import Path
import sys
root, path = map(lambda x: Path(x).resolve(), sys.argv[1:])
if not any(path.is_relative_to(root / directory) for directory in ('config', '.local')):
    raise SystemExit('NODE_OVERRIDES_FILE must remain under config/ or .local/')
CHECK_PATH

backup_dir=${repo_dir}/.local/draft-backup/$(date +%Y%m%d-%H%M%S)
mkdir -p "${backup_dir}" "${repo_dir}/.local"
if [[ -f "${overrides_file}" ]]; then
  cp -a "${overrides_file}" "${backup_dir}/node-overrides.json"
fi
mkdir -p "$(dirname -- "${overrides_file}")"
cp -a "${repo_dir}/config/sync.json" "${backup_dir}/sync.json"
cp -a "${repo_dir}/config/ansible-vars.json" "${backup_dir}/ansible-vars.json"
install -m 0644 "${draft_dir}/sync.json" "${repo_dir}/config/sync.json"
install -m 0644 "${draft_dir}/ansible-vars.json" "${repo_dir}/config/ansible-vars.json"
install -m 0600 "${draft_dir}/node-overrides.json" "${overrides_file}"
install -m 0600 "${draft_dir}/isuscope.json" "${repo_dir}/.local/isuscope-overrides.json"
# The reviewed switch to the Rust implementation, applied by switch-language-service.sh.
if [[ -f "${draft_dir}/service-switch.json" ]]; then
  jq -e '.start and (.stop | type == "array") and (.nodes | type == "array")' "${draft_dir}/service-switch.json" >/dev/null
  install -m 0600 "${draft_dir}/service-switch.json" "${repo_dir}/.local/service-switch.json"
else
  rm -f "${repo_dir}/.local/service-switch.json"
fi

echo "draft applied; tracked config changes require review"
echo "backup: ${backup_dir}"
echo "next: make discover"
