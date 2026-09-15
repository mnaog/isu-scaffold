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
transaction_dir=${sync_repo_dir}/.local/deploy-transactions
previous_release_file=${transaction_dir}/${release}.previous-release
previous_commit_file=${transaction_dir}/${release}.previous-commit

# 一部だけを戻す事故を避けるため、全backupが揃っていることを先に確認する。
while IFS=$'\t' read -r name node_group remote_path <&3; do
  backup=${remote_path}.isuscope-backup.${release}
  while IFS= read -r node <&4; do
    "${script_dir}/ssh-node.sh" "${node}" \
      "sudo test -e '${backup}' || sudo test -e '${backup}.absent'" || {
      echo "rollback backup is missing for ${name} on ${node}: ${release}" >&2
      exit 1
    }
  done 4< <(sync_group_nodes "${node_group}")
done 3< <(jq -r '.items[] | [.name, .node_group, .remote] | @tsv' "${sync_manifest}")

while IFS=$'\t' read -r name node_group remote_path <&3; do
  backup=${remote_path}.isuscope-backup.${release}
  while IFS= read -r node <&4; do
    printf 'rolling back %s on %s\n' "${name}" "${node}"
    "${script_dir}/ssh-node.sh" "${node}" \
      "set -eu; if sudo test -e '${backup}'; then if sudo test -e '${remote_path}'; then sudo mv '${remote_path}' '${remote_path}.isuscope-failed.${failed_suffix}'; fi; sudo mv '${backup}' '${remote_path}'; else sudo rm -rf '${remote_path}'; sudo rm -f '${backup}.absent'; fi"
  done 4< <(sync_group_nodes "${node_group}")
done 3< <(jq -r '.items[] | [.name, .node_group, .remote] | @tsv' "${sync_manifest}")

failed=0
while IFS=$'\t' read -r command_group command <&3; do
  while IFS= read -r node <&4; do
    printf 'restoring runtime on %s:%s\n' "${command_group}" "${node}"
    "${script_dir}/ssh-node.sh" "${node}" "${command}" || failed=1
  done 4< <(sync_group_nodes "${command_group}")
done 3< <(sync_commands rollback_commands)

"${script_dir}/status.sh" || failed=1
if [[ "${failed}" -eq 0 ]]; then
  if [[ -s "${previous_release_file}" ]]; then
    cp "${previous_release_file}" "${sync_repo_dir}/.local/current-release"
  elif [[ -f "${previous_release_file}" ]]; then
    rm -f -- "${sync_repo_dir}/.local/current-release"
  fi
  if [[ -s "${previous_commit_file}" ]]; then
    cp "${previous_commit_file}" "${sync_repo_dir}/.local/current-commit"
    cp "${previous_commit_file}" "${sync_repo_dir}/.local/current-deploy-commit"
  elif [[ -f "${previous_commit_file}" ]]; then
    rm -f -- "${sync_repo_dir}/.local/current-commit"
    rm -f -- "${sync_repo_dir}/.local/current-deploy-commit"
  fi
fi
exit "${failed}"
