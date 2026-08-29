#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=sync-lib.sh
source "${script_dir}/sync-lib.sh"
if [[ "${ISUCON_INTERNAL_OPERATION_LOCK_HELD:-false}" != true ]]; then
  exec "${script_dir}/with-operation-lock.sh" "$0" "$@"
fi
sync_validate
release=${1:-}
sync_validate_release "${release}"
failed_suffix=$(date +%Y%m%d%H%M%S)

while IFS=$'\t' read -r name node_group remote_path <&3; do
  backup=${remote_path}.isuscope-backup.${release}
  while IFS= read -r node <&4; do
    printf 'rolling back %s on %s\n' "${name}" "${node}"
    "${script_dir}/ssh-node.sh" "${node}" \
      "set -eu; if sudo test -e '${backup}'; then if sudo test -e '${remote_path}'; then sudo mv '${remote_path}' '${remote_path}.isuscope-failed.${failed_suffix}'; fi; sudo mv '${backup}' '${remote_path}'; elif sudo test -e '${backup}.absent'; then sudo rm -rf '${remote_path}'; sudo rm -f '${backup}.absent'; else echo 'no backup for ${name}; skipping' >&2; fi"
  done 4< <(sync_group_nodes "${node_group}")
done 3< <(jq -r '.items[] | [.name, .node_group, .remote] | @tsv' "${sync_manifest}")

"${script_dir}/status.sh"
