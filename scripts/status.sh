#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=sync-lib.sh
source "${script_dir}/sync-lib.sh"
sync_validate

failed=0
while IFS=$'\t' read -r command_group command <&3; do
  while IFS= read -r node <&4; do
    printf '[%s:%s]\n' "${command_group}" "${node}"
    "${script_dir}/ssh-node.sh" "${node}" "${command}" || failed=1
  done 4< <(sync_group_nodes "${command_group}")
done 3< <(sync_commands status_commands)
exit "${failed}"
