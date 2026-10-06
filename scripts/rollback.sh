#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=sync-lib.sh
source "${script_dir}/sync-lib.sh"
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi
sync_validate
release=${1:-}
sync_validate_release "${release}"
failed_suffix=$(date +%Y%m%d%H%M%S)
transaction_dir=${sync_repo_dir}/.local/deploy-transactions
previous_release_file=${transaction_dir}/${release}.previous-release
previous_commit_file=${transaction_dir}/${release}.previous-commit

# 対象をnode単位に並べ、確認・復元をそれぞれ1 node 1 SSHで全nodeへ並列に送る。
targets=$(mktemp)
trap 'rm -f -- "${targets}"' EXIT
while IFS=$'\t' read -r name node_group remote_path <&3; do
  while IFS= read -r node <&4; do
    printf '%s\t%s\t%s\n' "${node}" "${name}" "${remote_path}" >>"${targets}"
  done 4< <(sync_group_nodes "${node_group}")
done 3< <(jq -r '.items[] | [.name, .node_group, .remote] | @tsv' "${sync_manifest}")

check_node() {
  local target_node=$1 node name remote_path backup command="missing=''"
  while IFS=$'\t' read -r node name remote_path <&3; do
    [[ "${node}" == "${target_node}" ]] || continue
    backup=${remote_path}.isuscope-backup.${release}
    command+="; sudo test -e '${backup}' || sudo test -e '${backup}.absent' || missing=\"\$missing ${name}\""
  done 3<"${targets}"
  command+="; test -z \"\$missing\" || { echo \"rollback backup is missing for\${missing} on ${target_node}: ${release}\" >&2; exit 1; }"
  "${script_dir}/ssh-node.sh" "${target_node}" "${command}"
}

restore_node() {
  local target_node=$1 node name remote_path backup command="set -eu"
  while IFS=$'\t' read -r node name remote_path <&3; do
    [[ "${node}" == "${target_node}" ]] || continue
    backup=${remote_path}.isuscope-backup.${release}
    printf 'rolling back %s on %s\n' "${name}" "${node}"
    command+="; if sudo test -e '${backup}'; then if sudo test -e '${remote_path}'; then sudo mv '${remote_path}' '${remote_path}.isuscope-failed.${failed_suffix}'; fi; sudo mv '${backup}' '${remote_path}'; else sudo rm -rf '${remote_path}'; sudo rm -f '${backup}.absent'; fi"
  done 3<"${targets}"
  "${script_dir}/ssh-node.sh" "${target_node}" "${command}"
}

# Runs "$1 NODE" on every listed node in parallel; fails if any node failed.
on_nodes() {
  local action=$1 node pid failed=0
  local -a pids=()
  shift
  for node in "$@"; do
    "${action}" "${node}" &
    pids+=("$!")
  done
  for pid in "${pids[@]}"; do
    wait "${pid}" || failed=1
  done
  return "${failed}"
}

nodes=()
while IFS= read -r node; do nodes+=("${node}"); done < <(cut -f1 "${targets}" | awk '!seen[$0]++')
if [[ "${#nodes[@]}" -gt 0 ]]; then
  # 一部だけを戻す事故を避けるため、全backupが揃っていることを先に確認する。
  on_nodes check_node "${nodes[@]}" || exit 1
  on_nodes restore_node "${nodes[@]}" || exit 1
fi

failed=0
run_rollback_command() { "${script_dir}/ssh-node.sh" "$1" "${command}"; }
# コマンド間の順序（例: DB再起動の後にapp再起動）は保ち、同じコマンドの各nodeだけを並列にする。
while IFS=$'\t' read -r command_group command <&3; do
  group_nodes=()
  while IFS= read -r node <&4; do
    printf 'restoring runtime on %s:%s\n' "${command_group}" "${node}"
    group_nodes+=("${node}")
  done 4< <(sync_group_nodes "${command_group}")
  [[ "${#group_nodes[@]}" -eq 0 ]] || on_nodes run_rollback_command "${group_nodes[@]}" || failed=1
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
