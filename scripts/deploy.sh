#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=sync-lib.sh
source "${script_dir}/sync-lib.sh"
if [[ "${ISUCON_INTERNAL_OPERATION_LOCK_HELD:-false}" != true ]]; then
  exec "${script_dir}/with-operation-lock.sh" "$0" "$@"
fi
sync_validate
cd "${sync_repo_dir}"

git rev-parse --verify HEAD >/dev/null || { echo "create the initial commit before deploy" >&2; exit 1; }
release=${RELEASE:-$(git rev-parse --short=12 HEAD)}
sync_validate_release "${release}"
minimum_free_mb=$(jq -r '.minimum_free_mb_after_deploy // 1024' "${sync_manifest}")

case "${sync_manifest}" in
  "${sync_repo_dir}"/*) manifest_relative=${sync_manifest#"${sync_repo_dir}"/} ;;
  *) echo "deploy manifest must be inside the repository" >&2; exit 1 ;;
esac
git ls-files --error-unmatch -- "${manifest_relative}" >/dev/null 2>&1 || {
  echo "commit ${manifest_relative} before deploy" >&2
  exit 1
}
test -z "$(git status --porcelain -- "${manifest_relative}")" || {
  echo "commit ${manifest_relative} before deploy" >&2
  exit 1
}

while IFS= read -r local_path; do
  test -e "${sync_repo_dir}/${local_path}" || { echo "missing local sync path: ${local_path}" >&2; exit 1; }
  changes=$(git status --porcelain -- "${local_path}")
  test -z "${changes}" || {
    echo "commit ${local_path} before deploy" >&2
    printf '%s\n' "${changes}" >&2
    exit 1
  }
done < <(jq -r '.items[].local' "${sync_manifest}")

pre_deploy=$(jq -r '.pre_deploy_command' "${sync_manifest}")
if [[ -n "${pre_deploy}" ]]; then
  bash -c "${pre_deploy}"
  while IFS= read -r local_path; do
    changes=$(git status --porcelain -- "${local_path}")
    test -z "${changes}" || {
      echo "pre_deploy_command changed a committed sync path: ${local_path}" >&2
      printf '%s\n' "${changes}" >&2
      exit 1
    }
  done < <(jq -r '.items[].local' "${sync_manifest}")
fi

transaction_dir=${sync_repo_dir}/.local/deploy-transactions
plan_file=${transaction_dir}/${release}.tsv
state_file=${transaction_dir}/${release}.state
mkdir -p "${transaction_dir}"
: >"${plan_file}"
while IFS=$'\t' read -r name type node_group local_path remote_path owner owner_group <&3; do
  size_kb=$(du -sk "${sync_repo_dir}/${local_path}" | awk '{print $1}')
  while IFS= read -r node <&4; do
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "${name}" "${type}" "${node}" "${local_path}" "${remote_path}" \
      "${owner}" "${owner_group}" "${size_kb}" >>"${plan_file}"
  done 4< <(sync_group_nodes "${node_group}")
done 3< <(jq -r '.items[] | [.name, .type, .node_group, .local, .remote, .owner, .owner_group] | @tsv' "${sync_manifest}")

cleanup_staging() {
  local name type node local_path remote_path owner owner_group size_kb staging
  while IFS=$'\t' read -r name type node local_path remote_path owner owner_group size_kb <&3; do
    staging=${remote_path}.isuscope-staging.${release}
    "${script_dir}/ssh-node.sh" "${node}" \
      "sudo rm -rf '${staging}' '${staging}.dir'" >/dev/null 2>&1 || true
  done 3<"${plan_file}"
}

rollback_transaction() {
  local name type node local_path remote_path owner owner_group size_kb staging backup
  echo "rolling back deploy transaction ${release}" >&2
  while IFS=$'\t' read -r name type node local_path remote_path owner owner_group size_kb <&3; do
    staging=${remote_path}.isuscope-staging.${release}
    backup=${remote_path}.isuscope-backup.${release}
    "${script_dir}/ssh-node.sh" "${node}" \
      "set -eu; if sudo test -e '${backup}'; then sudo rm -rf '${remote_path}'; sudo mv '${backup}' '${remote_path}'; elif sudo test -e '${backup}.absent'; then sudo rm -rf '${remote_path}'; sudo rm -f '${backup}.absent'; fi; sudo rm -rf '${staging}' '${staging}.dir'" \
      >/dev/null 2>&1 || echo "rollback failed for ${name} on ${node}" >&2
  done 3<"${plan_file}"
}

abort_transaction() {
  transaction_exit=$1
  trap - EXIT INT TERM
  rollback_transaction
  cleanup_staging
  printf 'failed\n' >"${state_file}"
  exit "${transaction_exit}"
}
trap 'abort_transaction $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'preflight\n' >"${state_file}"
while IFS=$'\t' read -r name type node local_path remote_path owner owner_group size_kb <&3; do
  staging=${remote_path}.isuscope-staging.${release}
  backup=${remote_path}.isuscope-backup.${release}
  remote_parent=$(dirname -- "${remote_path}")
  node_size_kb=$(awk -F '\t' -v target="${node}" '$3 == target {total += $8} END {print total + 0}' "${plan_file}")
  required_kb=$((node_size_kb + minimum_free_mb * 1024))
  printf 'preflight %s on %s\n' "${name}" "${node}"
  "${script_dir}/ssh-node.sh" "${node}" \
    "set -eu; sudo -n true; sudo test -d '${remote_parent}'; getent passwd '${owner}' >/dev/null; getent group '${owner_group}' >/dev/null; sudo test ! -e '${staging}'; sudo test ! -e '${staging}.dir'; sudo test ! -e '${backup}'; sudo test ! -e '${backup}.absent'; available=\$(df -Pk '${remote_parent}' | awk 'NR==2 {print \$4}'); test \"\$available\" -ge '${required_kb}'"
done 3<"${plan_file}"

printf 'staging\n' >"${state_file}"
while IFS=$'\t' read -r name type node local_path remote_path owner owner_group size_kb <&3; do
  staging=${remote_path}.isuscope-staging.${release}
  printf 'staging %s on %s\n' "${name}" "${node}"
  if [[ "${type}" == directory ]]; then
    "${script_dir}/ssh-node.sh" "${node}" "sudo install -d -m 0755 '${staging}'"
    tar -C "${sync_repo_dir}/${local_path}" -cf - . | \
      "${script_dir}/ssh-node.sh" "${node}" "sudo tar -C '${staging}' -xf -"
  else
    remote_name=$(basename -- "${remote_path}")
    "${script_dir}/ssh-node.sh" "${node}" "sudo install -d -m 0755 '${staging}.dir'"
    tar -C "$(dirname -- "${sync_repo_dir}/${local_path}")" -cf - "$(basename -- "${local_path}")" | \
      "${script_dir}/ssh-node.sh" "${node}" "sudo tar -C '${staging}.dir' -xf -"
    "${script_dir}/ssh-node.sh" "${node}" \
      "sudo mv '${staging}.dir/$(basename -- "${local_path}")' '${staging}' && sudo rmdir '${staging}.dir'"
  fi
  "${script_dir}/ssh-node.sh" "${node}" "sudo chown -R '${owner}:${owner_group}' '${staging}'"
done 3<"${plan_file}"

printf 'building\n' >"${state_file}"
while IFS=$'\t' read -r build_name build_group build_item build_command <&3; do
  remote_path=$(jq -r --arg item "${build_item}" '.items[] | select(.name == $item) | .remote' \
    "${sync_manifest}")
  staging=${remote_path}.isuscope-staging.${release}
  while IFS= read -r node <&4; do
    printf 'building %s on %s\n' "${build_name}" "${node}"
    "${script_dir}/ssh-node.sh" "${node}" \
      "set -eu; export ISUCON_DEPLOY_RELEASE='${release}'; export ISUCON_DEPLOY_REMOTE_PATH='${remote_path}'; export ISUCON_DEPLOY_STAGING_PATH='${staging}'; ${build_command}"
  done 4< <(sync_group_nodes "${build_group}")
done 3< <(sync_build_commands)

printf 'switching\n' >"${state_file}"
while IFS=$'\t' read -r name type node local_path remote_path owner owner_group size_kb <&3; do
  staging=${remote_path}.isuscope-staging.${release}
  backup=${remote_path}.isuscope-backup.${release}
  printf 'switching %s on %s\n' "${name}" "${node}"
  "${script_dir}/ssh-node.sh" "${node}" \
    "set -eu; sudo test -e '${staging}'; if sudo test -e '${remote_path}'; then sudo mv '${remote_path}' '${backup}'; else sudo touch '${backup}.absent'; fi; sudo mv '${staging}' '${remote_path}'"
done 3<"${plan_file}"

printf 'validating\n' >"${state_file}"
while IFS=$'\t' read -r command_group command <&3; do
  while IFS= read -r node <&4; do
    "${script_dir}/ssh-node.sh" "${node}" "${command}"
  done 4< <(sync_group_nodes "${command_group}")
done 3< <(sync_commands post_deploy_commands)

trap - EXIT INT TERM
cleanup_staging
printf 'complete\n' >"${state_file}"
printf '%s\n' "${release}" >"${sync_repo_dir}/.local/current-release"
echo "deploy complete: ${release}"
