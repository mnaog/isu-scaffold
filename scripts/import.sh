#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=sync-lib.sh
source "${script_dir}/sync-lib.sh"
if [[ "${ISUCON_INTERNAL_OPERATION_LOCK_HELD:-false}" != true ]]; then
  exec "${script_dir}/with-operation-lock.sh" "$0" "$@"
fi
sync_validate
command -v python3 >/dev/null

comparison_file=${sync_repo_dir}/.local/import-comparison.tsv
backup_root=${sync_repo_dir}/.local/import-backup/$(date +%Y%m%d-%H%M%S)
mkdir -p "${sync_repo_dir}/.local"
printf 'item\tnode\tdigest\tsource\n' >"${comparison_file}"

divergent=0
while IFS=$'\t' read -r name node_group remote_path item_source <&3; do
  source_digest=
  item_digest=
  while IFS= read -r node <&4; do
    digest=$("${script_dir}/ssh-node.sh" "${node}" \
      "sudo python3 /usr/local/lib/isuscope/tree-digest.py '${remote_path}'" | tail -n 1)
    [[ "${digest}" =~ ^[0-9a-f]{64}$ ]] || {
      echo "invalid digest for ${name} on ${node}: ${digest}" >&2
      exit 1
    }
    if [[ -z "${item_digest}" ]]; then
      item_digest=${digest}
    elif [[ "${digest}" != "${item_digest}" ]]; then
      divergent=1
    fi
    if [[ "${node}" == "${item_source}" ]]; then
      source_digest=${digest}
    fi
    is_source=false
    [[ "${node}" == "${item_source}" ]] && is_source=true
    printf '%s\t%s\t%s\t%s\n' "${name}" "${node}" "${digest}" "${is_source}" >>"${comparison_file}"
  done 4< <(sync_group_nodes "${node_group}")
  test -n "${source_digest}" || { echo "source digest missing for ${name}" >&2; exit 1; }
done 3< <(jq -r '.source_node as $default | .items[] | [.name, .node_group, .remote, (.source_node // $default)] | @tsv' "${sync_manifest}")

column -t -s $'\t' "${comparison_file}" 2>/dev/null || cat "${comparison_file}"
if [[ "${divergent}" -ne 0 && "${IMPORT_ALLOW_DIVERGENT:-false}" != true ]]; then
  echo "remote sync targets differ between nodes; choose the source deliberately and rerun with IMPORT_ALLOW_DIVERGENT=true" >&2
  exit 1
fi

staging=
cleanup() {
  if [[ -n "${staging}" && -d "${staging}" ]]; then
    find "${staging}" -depth -delete
  fi
}
trap cleanup EXIT

while IFS=$'\t' read -r name type local_path remote_path item_source <&3; do
  local_absolute=${sync_repo_dir}/${local_path}
  local_parent=$(dirname -- "${local_absolute}")
  mkdir -p "${local_parent}"
  staging=$(mktemp -d "${local_parent}/.isuscope-import.XXXXXX")
  printf 'importing %s from %s:%s\n' "${name}" "${item_source}" "${remote_path}"
  if [[ "${type}" == directory ]]; then
    "${script_dir}/ssh-node.sh" "${item_source}" \
      "sudo tar -C '${remote_path}' -cf - ." | tar -C "${staging}" -xf -
  else
    remote_parent=$(dirname -- "${remote_path}")
    remote_name=$(basename -- "${remote_path}")
    "${script_dir}/ssh-node.sh" "${item_source}" \
      "sudo tar -C '${remote_parent}' -cf - '${remote_name}'" | tar -C "${staging}" -xf -
    mv -- "${staging}/${remote_name}" "${staging}/item"
  fi
  if [[ -e "${local_absolute}" ]]; then
    mkdir -p "${backup_root}/$(dirname -- "${local_path}")"
    mv -- "${local_absolute}" "${backup_root}/${local_path}"
  fi
  if [[ "${type}" == directory ]]; then
    mv -- "${staging}" "${local_absolute}"
  else
    mv -- "${staging}/item" "${local_absolute}"
    rmdir "${staging}"
  fi
  staging=

  local_digest=$(python3 "${script_dir}/tree-digest.py" "${local_absolute}")
  source_digest=$(awk -F '\t' -v item="${name}" -v node="${item_source}" \
    'NR > 1 && $1 == item && $2 == node { print $3; exit }' "${comparison_file}")
  test "${local_digest}" == "${source_digest}" || {
    echo "digest mismatch after import: ${name} local=${local_digest} remote=${source_digest}" >&2
    exit 1
  }
done 3< <(jq -r '.source_node as $default | .items[] | [.name, .type, .local, .remote, (.source_node // $default)] | @tsv' "${sync_manifest}")
trap - EXIT

echo "import complete; all local digests match their selected source nodes"
if [[ -d "${backup_root}" ]]; then
  echo "previous local files: ${backup_root}"
fi
