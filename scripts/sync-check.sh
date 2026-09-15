#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=sync-lib.sh
source "${script_dir}/sync-lib.sh"
sync_validate

tracked_bytes=0
replicated_bytes=0
if git -C "${sync_repo_dir}" rev-parse --verify HEAD >/dev/null 2>&1; then
  while IFS=$'\t' read -r local_path node_group; do
    if git -C "${sync_repo_dir}" cat-file -e "HEAD:${local_path}" 2>/dev/null; then
      item_bytes=$(git -C "${sync_repo_dir}" ls-tree -r -l HEAD -- "${local_path}" |
        awk '{total += $4} END {print total + 0}')
      node_count=$(sync_group_nodes "${node_group}" | awk 'END {print NR + 0}')
      tracked_bytes=$((tracked_bytes + item_bytes))
      replicated_bytes=$((replicated_bytes + item_bytes * node_count))
    fi
  done < <(jq -r '.items[] | [.local, .node_group] | @tsv' "${sync_manifest}")
  echo "sync manifest is valid (tracked payload ${tracked_bytes} bytes, replicated ${replicated_bytes} bytes)"
else
  echo "sync manifest is valid"
fi
