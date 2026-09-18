#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=sync-lib.sh
source "${script_dir}/sync-lib.sh"
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi
sync_validate
cd "${sync_repo_dir}"

git rev-parse --verify HEAD >/dev/null || { echo "create the initial commit before deploy" >&2; exit 1; }
head_commit=$(git rev-parse HEAD)
commit_short=$(git rev-parse --short=12 HEAD)
release=${RELEASE:-${commit_short}-$(date -u +%Y%m%dT%H%M%SZ)-$$}
sync_validate_release "${release}"
minimum_free_mb=$(jq -r '.minimum_free_mb_after_deploy // 1024' "${sync_manifest}")
max_parallel_nodes=${DEPLOY_MAX_PARALLEL_NODES:-5}
[[ "${max_parallel_nodes}" =~ ^[1-9][0-9]*$ ]] || {
  echo "DEPLOY_MAX_PARALLEL_NODES must be a positive integer" >&2
  exit 2
}
backup_retention=${DEPLOY_BACKUP_RETENTION:-3}
[[ "${backup_retention}" =~ ^[1-9][0-9]*$ ]] || {
  echo "DEPLOY_BACKUP_RETENTION must be a positive integer" >&2
  exit 2
}

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

previous_commit=
previous_commit_candidate=
if [[ -s "${sync_repo_dir}/.local/current-deploy-commit" ]]; then
  previous_commit_candidate=$(cat "${sync_repo_dir}/.local/current-deploy-commit")
elif [[ -s "${sync_repo_dir}/.local/current-release" ]]; then
  # Backward compatibility for deployments made before current-deploy-commit existed.
  previous_commit_candidate=$(cat "${sync_repo_dir}/.local/current-release")
fi
if [[ -n "${previous_commit_candidate}" ]] &&
  git rev-parse --verify "${previous_commit_candidate}^{commit}" >/dev/null 2>&1; then
  previous_commit=$(git rev-parse "${previous_commit_candidate}^{commit}")
fi

while IFS= read -r local_path; do
  test -e "${sync_repo_dir}/${local_path}" || { echo "missing local sync path: ${local_path}" >&2; exit 1; }
  git cat-file -e "HEAD:${local_path}" 2>/dev/null || {
    echo "sync path is not committed at HEAD: ${local_path}" >&2
    exit 1
  }
  changes=$(git status --porcelain --untracked-files=all -- "${local_path}")
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
    changes=$(git status --porcelain --untracked-files=all -- "${local_path}")
    test -z "${changes}" || {
      echo "pre_deploy_command changed a committed sync path: ${local_path}" >&2
      printf '%s\n' "${changes}" >&2
      exit 1
    }
  done < <(jq -r '.items[].local' "${sync_manifest}")
fi

transaction_dir=${sync_repo_dir}/.local/deploy-transactions
plan_file=${transaction_dir}/${release}.tsv
build_plan_file=${transaction_dir}/${release}.build.tsv
post_plan_file=${transaction_dir}/${release}.post.tsv
rollback_plan_file=${transaction_dir}/${release}.rollback.tsv
nodes_file=${transaction_dir}/${release}.nodes
state_file=${transaction_dir}/${release}.state
timing_file=${transaction_dir}/${release}.timing.tsv
commit_file=${transaction_dir}/${release}.commit
previous_release_file=${transaction_dir}/${release}.previous-release
previous_commit_file=${transaction_dir}/${release}.previous-commit
mkdir -p "${transaction_dir}"
if [[ -e "${state_file}" ]]; then
  # 同じrelease名を再利用すると、前回の退避（*.isuscope-backup.<release>）を
  # 今回のものと誤認し、失敗時に古い版へ戻してしまう。
  echo "release ${release} was already used: ${state_file}" >&2
  echo "unset RELEASE, or choose a name that has not been deployed" >&2
  exit 1
fi
: >"${plan_file}"
: >"${build_plan_file}"
: >"${post_plan_file}"
: >"${rollback_plan_file}"
: >"${nodes_file}"
: >"${timing_file}"
printf '%s\n' "${head_commit}" >"${commit_file}"
if [[ -f "${sync_repo_dir}/.local/current-release" ]]; then
  cp "${sync_repo_dir}/.local/current-release" "${previous_release_file}"
else
  : >"${previous_release_file}"
fi
if [[ -f "${sync_repo_dir}/.local/current-commit" ]]; then
  cp "${sync_repo_dir}/.local/current-commit" "${previous_commit_file}"
else
  : >"${previous_commit_file}"
fi

while IFS=$'\t' read -r name type node_group local_path remote_path owner owner_group <&3; do
  size_bytes=$(git ls-tree -r -l HEAD -- "${local_path}" | awk '{total += $4} END {print total + 0}')
  size_kb=$(((size_bytes + 1023) / 1024))
  while IFS= read -r node <&4; do
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "${name}" "${type}" "${node}" "${local_path}" "${remote_path}" \
      "${owner}" "${owner_group}" "${size_kb}" >>"${plan_file}"
  done 4< <(sync_group_nodes "${node_group}")
done 3< <(jq -r '.items[] | [.name, .type, .node_group, .local, .remote, .owner, .owner_group] | @tsv' "${sync_manifest}")

build_inputs_unchanged() {
  local build_name=$1 build_item=$2 local_path previous_tree current_tree
  local previous_definition current_definition
  [[ -n "${previous_commit}" ]] || return 1
  local_path=$(jq -r --arg item "${build_item}" '.items[] | select(.name == $item) | .local' \
    "${sync_manifest}")
  previous_tree=$(git rev-parse "${previous_commit}:${local_path}" 2>/dev/null) || return 1
  current_tree=$(git rev-parse "HEAD:${local_path}")
  [[ "${previous_tree}" == "${current_tree}" ]] || return 1
  previous_definition=$(git show "${previous_commit}:${manifest_relative}" 2>/dev/null | \
    jq -ceS --arg name "${build_name}" \
      'first((.build_commands // [])[] | select(.name == $name))' 2>/dev/null) || return 1
  current_definition=$(jq -ceS --arg name "${build_name}" \
    'first((.build_commands // [])[] | select(.name == $name))' "${sync_manifest}")
  [[ "${previous_definition}" == "${current_definition}" ]]
}

build_input_id() {
  local build_name=$1 build_item=$2 local_path current_tree current_definition
  local_path=$(jq -r --arg item "${build_item}" '.items[] | select(.name == $item) | .local' \
    "${sync_manifest}")
  current_tree=$(git rev-parse "HEAD:${local_path}")
  current_definition=$(jq -ceS --arg name "${build_name}" \
    'first((.build_commands // [])[] | select(.name == $name))' "${sync_manifest}")
  printf '%s\n%s\n' "${current_tree}" "${current_definition}" | git hash-object --stdin
}

while IFS=$'\t' read -r build_name build_group build_item build_command <&3; do
  remote_path=$(jq -r --arg item "${build_item}" '.items[] | select(.name == $item) | .remote' \
    "${sync_manifest}")
  inputs_unchanged=false
  if build_inputs_unchanged "${build_name}" "${build_item}"; then
    inputs_unchanged=true
  fi
  input_id=$(build_input_id "${build_name}" "${build_item}")
  while IFS= read -r node <&4; do
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "${node}" "${build_name}" "${build_item}" "${remote_path}" \
      "${inputs_unchanged}" "${input_id}" "${build_command}" \
      >>"${build_plan_file}"
  done 4< <(sync_group_nodes "${build_group}")
done 3< <(sync_build_commands)

expand_command_plan() {
  local command_key=$1 output_file=$2 command_group command node
  while IFS=$'\t' read -r command_group command <&3; do
    while IFS= read -r node <&4; do
      printf '%s\t%s\n' "${node}" "${command}" >>"${output_file}"
    done 4< <(sync_group_nodes "${command_group}")
  done 3< <(sync_commands "${command_key}")
}
expand_command_plan post_deploy_commands "${post_plan_file}"
expand_command_plan rollback_commands "${rollback_plan_file}"
awk -F '\t' '{print $3}' "${plan_file}" | sort -u >"${nodes_file}"

deploy_started_epoch=$(date +%s)
active_pids=()
mark_phase() {
  local phase=$1 now elapsed
  now=$(date +%s)
  elapsed=$((now - deploy_started_epoch))
  printf '%s\n' "${phase}" >"${state_file}"
  printf '%s\t%s\t%s\n' "${phase}" "${now}" "${elapsed}" >>"${timing_file}"
  printf '[deploy +%ss] %s\n' "${elapsed}" "${phase}"
}

run_node_phase() {
  local phase=$1 node pid failed=0
  active_pids=()
  while IFS= read -r node; do
    (trap - EXIT INT TERM; "${phase}_node" "${node}") &
    active_pids+=("$!")
    if [[ "${#active_pids[@]}" -ge "${max_parallel_nodes}" ]]; then
      for pid in "${active_pids[@]}"; do
        if ! wait "${pid}"; then failed=1; fi
      done
      active_pids=()
    fi
  done <"${nodes_file}"
  if [[ "${#active_pids[@]}" -gt 0 ]]; then
    for pid in "${active_pids[@]}"; do
      if ! wait "${pid}"; then failed=1; fi
    done
  fi
  active_pids=()
  return "${failed}"
}

cancel_active_jobs() {
  local pid
  [[ "${#active_pids[@]}" -gt 0 ]] || return 0
  for pid in "${active_pids[@]}"; do
    pkill -TERM -P "${pid}" 2>/dev/null || true
    kill -TERM "${pid}" 2>/dev/null || true
  done
  for pid in "${active_pids[@]}"; do
    wait "${pid}" 2>/dev/null || true
  done
  active_pids=()
}

preflight_node() {
  local target_node=$1 name type node local_path remote_path owner owner_group size_kb
  local staging backup remote_parent required_kb node_size_kb command
  node_size_kb=$(awk -F '\t' -v target="${target_node}" '$3 == target {total += $8} END {print total + 0}' "${plan_file}")
  required_kb=$((node_size_kb + minimum_free_mb * 1024))
  command="set -eu; sudo -n true"
  while IFS=$'\t' read -r name type node local_path remote_path owner owner_group size_kb <&3; do
    [[ "${node}" == "${target_node}" ]] || continue
    staging=${remote_path}.isuscope-staging.${release}
    backup=${remote_path}.isuscope-backup.${release}
    remote_parent=$(dirname -- "${remote_path}")
    command+="; sudo test -d '${remote_parent}'; getent passwd '${owner}' >/dev/null; getent group '${owner_group}' >/dev/null; sudo test ! -e '${staging}'; sudo test ! -e '${staging}.dir'; sudo test ! -e '${backup}'; sudo test ! -e '${backup}.absent'; available=\$(df -Pk '${remote_parent}' | awk 'NR==2 {print \$4}'); test \"\$available\" -ge '${required_kb}'"
  done 3<"${plan_file}"
  command+="; sudo test ! -e '/tmp/isucon-deploy-${release}'"
  printf 'preflight on %s\n' "${target_node}"
  "${script_dir}/ssh-node.sh" "${target_node}" "${command}"
}

staging_node() {
  local target_node=$1 name type node local_path remote_path owner owner_group size_kb
  local staging bundle command
  local -a local_paths=()
  bundle=/tmp/isucon-deploy-${release}
  command="set -eu; sudo install -d -m 0755 '${bundle}'; sudo tar -C '${bundle}' -xf -"
  while IFS=$'\t' read -r name type node local_path remote_path owner owner_group size_kb <&3; do
    [[ "${node}" == "${target_node}" ]] || continue
    staging=${remote_path}.isuscope-staging.${release}
    local_paths+=("${local_path}")
    command+="; sudo test -e '${bundle}/${local_path}'; sudo mv '${bundle}/${local_path}' '${staging}'; sudo chown -R '${owner}:${owner_group}' '${staging}'"
  done 3<"${plan_file}"
  command+="; sudo rm -rf '${bundle}'"
  printf 'staging %s committed paths on %s\n' "${#local_paths[@]}" "${target_node}"
  git archive --format=tar HEAD -- "${local_paths[@]}" | \
    "${script_dir}/ssh-node.sh" "${target_node}" "${command}"
}

building_node() {
  local target_node=$1 node build_name build_item remote_path inputs_unchanged input_id build_command staging
  while IFS=$'\t' read -r node build_name build_item remote_path inputs_unchanged input_id build_command <&3; do
    [[ "${node}" == "${target_node}" ]] || continue
    staging=${remote_path}.isuscope-staging.${release}
    printf 'building %s on %s\n' "${build_name}" "${target_node}"
    "${script_dir}/ssh-node.sh" "${target_node}" \
      "set -eu; export ISUCON_DEPLOY_RELEASE='${release}'; export ISUCON_DEPLOY_REMOTE_PATH='${remote_path}'; export ISUCON_DEPLOY_STAGING_PATH='${staging}'; export ISUCON_DEPLOY_BUILD_INPUTS_UNCHANGED='${inputs_unchanged}'; export ISUCON_DEPLOY_BUILD_INPUT_ID='${input_id}'; ${build_command}"
  done 3<"${build_plan_file}"
}

switching_node() {
  local target_node=$1 name type node local_path remote_path owner owner_group size_kb staging backup command
  command="set -eu"
  while IFS=$'\t' read -r name type node local_path remote_path owner owner_group size_kb <&3; do
    [[ "${node}" == "${target_node}" ]] || continue
    staging=${remote_path}.isuscope-staging.${release}
    backup=${remote_path}.isuscope-backup.${release}
    printf 'switching %s on %s\n' "${name}" "${target_node}"
    command+="; sudo test -e '${staging}'; if sudo test -e '${remote_path}'; then sudo mv '${remote_path}' '${backup}'; sudo touch '${backup}'; else sudo touch '${backup}.absent'; fi; sudo mv '${staging}' '${remote_path}'"
  done 3<"${plan_file}"
  "${script_dir}/ssh-node.sh" "${target_node}" "${command}"
}

run_commands_node() {
  local target_node=$1 command_file=$2 node remote_command
  while IFS=$'\t' read -r node remote_command <&3; do
    [[ "${node}" == "${target_node}" ]] || continue
    "${script_dir}/ssh-node.sh" "${target_node}" \
      "set -eu; export ISUCON_DEPLOY_RELEASE='${release}'; ${remote_command}"
  done 3<"${command_file}"
}

validating_node() { run_commands_node "$1" "${post_plan_file}"; }
rollback_commands_node() { run_commands_node "$1" "${rollback_plan_file}"; }

cleanup_node() {
  local target_node=$1 name type node local_path remote_path owner owner_group size_kb staging command
  command="set -u"
  while IFS=$'\t' read -r name type node local_path remote_path owner owner_group size_kb <&3; do
    [[ "${node}" == "${target_node}" ]] || continue
    staging=${remote_path}.isuscope-staging.${release}
    command+="; sudo rm -rf '${staging}' '${staging}.dir'"
  done 3<"${plan_file}"
  command+="; sudo rm -rf '/tmp/isucon-deploy-${release}'"
  "${script_dir}/ssh-node.sh" "${target_node}" "${command}" >/dev/null 2>&1 || true
}

cleanup_staging() { run_node_phase cleanup || true; }

restore_node() {
  local target_node=$1 name type node local_path remote_path owner owner_group size_kb staging backup command
  command="set -u; failed=0"
  while IFS=$'\t' read -r name type node local_path remote_path owner owner_group size_kb <&3; do
    [[ "${node}" == "${target_node}" ]] || continue
    staging=${remote_path}.isuscope-staging.${release}
    backup=${remote_path}.isuscope-backup.${release}
    # 段階ごとに結果を見る。まとめて`||`にすると、最後のrmの成功が
    # mvの失敗を隠し、復旧できていないのにdeployが成功扱いで終わる。
    command+="; if sudo test -e '${backup}'; then"
    command+="   if sudo rm -rf '${remote_path}'; then"
    command+="     sudo mv '${backup}' '${remote_path}' || { echo 'restore: mv ${backup} -> ${remote_path} failed' >&2; failed=1; };"
    command+="   else echo 'restore: rm ${remote_path} failed' >&2; failed=1; fi;"
    command+=" elif sudo test -e '${backup}.absent'; then"
    command+="   sudo rm -rf '${remote_path}' || { echo 'restore: rm ${remote_path} failed' >&2; failed=1; };"
    command+="   sudo rm -f '${backup}.absent' || { echo 'restore: rm ${backup}.absent failed' >&2; failed=1; };"
    command+=" fi"
    command+="; sudo rm -rf '${staging}' '${staging}.dir' || { echo 'restore: rm ${staging} failed' >&2; failed=1; }"
  done 3<"${plan_file}"
  command+="; sudo rm -rf '/tmp/isucon-deploy-${release}' || failed=1; exit \"\$failed\""
  # 復旧の失敗は最も知りたい出力なので、捨てずに操作者へ見せる。
  "${script_dir}/ssh-node.sh" "${target_node}" "${command}" || {
    echo "restore failed on ${target_node}" >&2
    return 1
  }
}

rollback_transaction() {
  local failed_state
  failed_state=$(cat "${state_file}" 2>/dev/null || true)
  # preflightはremoteを一切変えないため、復旧対象がない。ここで復旧を走らせると
  # 触るべきでないremote_pathへrm/mvを出すことになる。
  if [[ "${failed_state}" == preflight || -z "${failed_state}" ]]; then
    echo "deploy transaction ${release} failed before any remote change" >&2
    return 0
  fi
  echo "rolling back deploy transaction ${release}" >&2
  run_node_phase restore || echo "one or more nodes failed to restore transaction ${release}" >&2
  case "${failed_state}" in
    switching|validating)
      run_node_phase rollback_commands || echo "one or more rollback commands failed for ${release}" >&2
      ;;
  esac
}

abort_transaction() {
  transaction_exit=$1
  trap - EXIT INT TERM
  cancel_active_jobs
  rollback_transaction
  cleanup_staging
  printf 'failed\n' >"${state_file}"
  exit "${transaction_exit}"
}
trap 'abort_transaction $?' EXIT
trap 'cancel_active_jobs; exit 130' INT
trap 'cancel_active_jobs; exit 143' TERM

mark_phase preflight
run_node_phase preflight
mark_phase staging
run_node_phase staging
mark_phase building
run_node_phase building
mark_phase switching
run_node_phase switching
mark_phase validating
run_node_phase validating

trap - EXIT INT TERM
cleanup_staging
mark_phase complete
printf '%s\n' "${release}" >"${sync_repo_dir}/.local/current-release"
printf '%s\n' "${head_commit}" >"${sync_repo_dir}/.local/current-commit"
printf '%s\n' "${head_commit}" >"${sync_repo_dir}/.local/current-deploy-commit.tmp"
mv "${sync_repo_dir}/.local/current-deploy-commit.tmp" \
  "${sync_repo_dir}/.local/current-deploy-commit"

# rollback用backupは新しいものだけを残す。prune失敗はdeploy成功を取り消さない。
while IFS=$'\t' read -r name type node local_path remote_path owner owner_group size_kb <&3; do
  remote_parent=$(dirname -- "${remote_path}")
  remote_name=$(basename -- "${remote_path}")
  "${script_dir}/ssh-node.sh" "${node}" \
    "set -eu; count=0; sudo find '${remote_parent}' -mindepth 1 -maxdepth 1 -name '${remote_name}.isuscope-backup.*' -printf '%T@ %p\\n' | sort -rn | cut -d' ' -f2- | while IFS= read -r path; do count=\$((count + 1)); if [ \"\$count\" -gt '${backup_retention}' ]; then sudo rm -rf -- \"\$path\"; fi; done" \
    >/dev/null 2>&1 || echo "warning: failed to prune old backups for ${name} on ${node}" >&2
done 3<"${plan_file}"
deploy_elapsed=$(($(date +%s) - deploy_started_epoch))
echo "deploy complete: ${release} (${deploy_elapsed}s)"
