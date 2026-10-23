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
artifact_file=${transaction_dir}/${release}.artifacts.json
mkdir -p "${transaction_dir}"
if [[ -e "${state_file}" ]]; then
  # 同じrelease名を再利用すると、前回の退避（*.isuscope-backup.<release>）を
  # 今回のものと誤認し、失敗時に古い版へ戻してしまう。
  echo "release ${release} was already used: ${state_file}" >&2
  echo "unset RELEASE, or choose a name that has not been deployed" >&2
  exit 1
fi
# Send only what changed since the last successful deploy (scripts/deploy-plan.py): unchanged
# items are neither staged nor switched, and commands with "when" run only for their items.
# DEPLOY_ALL=1 sends everything, e.g. after the servers were changed outside deploy.
changed_file=${transaction_dir}/${release}.changed
python3 "${script_dir}/deploy-plan.py" changed --manifest "${sync_manifest}" \
  --previous "${previous_commit}" --head "${head_commit}" | sed '/^$/d' >"${changed_file}"
if [[ ! -s "${changed_file}" ]]; then
  rm -f -- "${changed_file}"
  printf '%s\n' "${head_commit}" >"${sync_repo_dir}/.local/current-commit"
  printf '%s\n' "${head_commit}" >"${sync_repo_dir}/.local/current-deploy-commit"
  echo "no sync item changed since the deployed ${previous_commit:0:12}; nothing to send (recorded ${commit_short} as deployed)"
  exit 0
fi
echo "changed items: $(tr '\n' ' ' <"${changed_file}")"
# Complete the local build once, before contacting or mutating any node. A build that no
# changed item uses is skipped: the binary on the servers is already the right one.
if jq -e --rawfile changed "${changed_file}" \
  '(.local_builds // []) as $builds | ([$builds[].item] - ($changed | split("\n")) | length) < ($builds | length)' \
  "${sync_manifest}" >/dev/null; then
  python3 "${script_dir}/local-build.py" build --strict --manifest "${sync_manifest}" --index "${artifact_file}"
else
  echo '[]' >"${artifact_file}"
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
  grep -qxF -- "${name}" "${changed_file}" || continue
  size_bytes=$(git ls-tree -r -l HEAD -- "${local_path}" | awk '{total += $4} END {print total + 0}')
  artifact_bytes=$(jq --arg item "${name}" '[.[] | select(.item == $item) | .size] | add // 0' "${artifact_file}")
  size_bytes=$((size_bytes + artifact_bytes))
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
  grep -qxF -- "${build_item}" "${changed_file}" || continue
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
  done 3< <(python3 "${script_dir}/deploy-plan.py" commands --manifest "${sync_manifest}" \
    --key "${command_key}" --changed "${changed_file}")
}
# The two plans are independent: read them side by side (one Python start each).
expand_command_plan post_deploy_commands "${post_plan_file}" &
post_plan_pid=$!
expand_command_plan rollback_commands "${rollback_plan_file}" &
rollback_plan_pid=$!
wait "${post_plan_pid}"
wait "${rollback_plan_pid}"
# Nodes that receive an item, and nodes that only run a selected command (a reload elsewhere).
{ awk -F '\t' '{print $3}' "${plan_file}"; cut -f1 "${post_plan_file}"; } | sed '/^$/d' | sort -u >"${nodes_file}"

deploy_started_epoch=$(date +%s)
active_pids=()
mark_stage() {
  local stage=$1 now elapsed
  now=$(date +%s)
  elapsed=$((now - deploy_started_epoch))
  printf '%s\n' "${stage}" >"${state_file}"
  printf '%s\t%s\t%s\n' "${stage}" "${now}" "${elapsed}" >>"${timing_file}"
  printf '[deploy +%ss] %s\n' "${elapsed}" "${stage}"
}

# 1 node 1 SSHにまとめる。1 itemの失敗で同じnodeの他itemを止めない。
pruning_node() {
  local target_node=$1 name type node local_path remote_path owner owner_group size_kb
  local remote_parent remote_name command="failed=''"
  while IFS=$'\t' read -r name type node local_path remote_path owner owner_group size_kb <&3; do
    [[ "${node}" == "${target_node}" ]] || continue
    remote_parent=$(dirname -- "${remote_path}")
    remote_name=$(basename -- "${remote_path}")
    command+="; (set -eu; count=0; sudo find '${remote_parent}' -mindepth 1 -maxdepth 1 -name '${remote_name}.isuscope-backup.*' -printf '%T@ %p\\n' | sort -rn | cut -d' ' -f2- | while IFS= read -r path; do count=\$((count + 1)); if [ \"\$count\" -gt '${backup_retention}' ]; then sudo rm -rf -- \"\$path\"; fi; done) || failed=\"\$failed ${name}\""
  done 3<"${plan_file}"
  command+="; test -z \"\$failed\" || { echo \"\$failed\"; exit 1; }"
  local failed_items
  failed_items=$("${script_dir}/ssh-node.sh" "${target_node}" "${command}" 2>/dev/null) ||
    echo "warning: failed to prune old backups on ${target_node}:${failed_items:- (connection)}" >&2
}

run_node_stage() {
  local stage=$1 node pid failed=0
  active_pids=()
  while IFS= read -r node; do
    (trap - EXIT INT TERM; "${stage}_node" "${node}") &
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
  local staging backup remote_parent required_kb node_size_kb command target
  node_size_kb=$(awk -F '\t' -v target="${target_node}" '$3 == target {total += $8} END {print total + 0}' "${plan_file}")
  required_kb=$((node_size_kb + minimum_free_mb * 1024))
  command="set -eu; sudo -n true"
  while IFS=$'\t' read -r name type node local_path remote_path owner owner_group size_kb <&3; do
    [[ "${node}" == "${target_node}" ]] || continue
    staging=${remote_path}.isuscope-staging.${release}
    backup=${remote_path}.isuscope-backup.${release}
    remote_parent=$(dirname -- "${remote_path}")
    command+="; sudo test -d '${remote_parent}'; getent passwd '${owner}' >/dev/null; getent group '${owner_group}' >/dev/null; sudo test ! -e '${staging}'; sudo test ! -e '${staging}.dir'; sudo test ! -e '${backup}'; sudo test ! -e '${backup}.absent'; available=\$(df -Pk '${remote_parent}' | awk 'NR==2 {print \$4}'); test \"\$available\" -ge '${required_kb}'"
    target=$(jq -r --arg item "${name}" '.[] | select(.item == $item) | .target' "${artifact_file}")
    case "${target}" in
      linux/amd64) command+="; test \"\$(uname -m)\" = x86_64" ;;
      linux/arm64) command+="; test \"\$(uname -m)\" = aarch64" ;;
    esac
  done 3<"${plan_file}"
  command+="; sudo test ! -e '/tmp/isucon-deploy-${release}'"
  printf 'preflight on %s\n' "${target_node}"
  "${script_dir}/ssh-node.sh" "${target_node}" "${command}"
}

staging_node() {
  local target_node=$1 name type node local_path remote_path owner owner_group size_kb
  local staging bundle command artifact_destination artifact_sha
  local -a local_paths=()
  bundle=/tmp/isucon-deploy-${release}
  command="set -eu; sudo install -d -m 0755 '${bundle}'; sudo tar -C '${bundle}' -xf -"
  while IFS=$'\t' read -r name type node local_path remote_path owner owner_group size_kb <&3; do
    [[ "${node}" == "${target_node}" ]] || continue
    staging=${remote_path}.isuscope-staging.${release}
    local_paths+=("${local_path}")
    command+="; sudo test -e '${bundle}/${local_path}'; sudo mv '${bundle}/${local_path}' '${staging}'; sudo chown -R '${owner}:${owner_group}' '${staging}'"
    while IFS=$'\t' read -r artifact_destination artifact_sha; do
      command+="; printf '%s  %s\\n' '${artifact_sha}' '${staging}/${artifact_destination}' | sudo sha256sum -c -; sudo test -x '${staging}/${artifact_destination}'; dependencies=\$(sudo ldd '${staging}/${artifact_destination}' 2>&1 || true); printf '%s\\n' \"\$dependencies\"; if printf '%s\\n' \"\$dependencies\" | grep -q 'not found'; then exit 1; fi"
    done < <(jq -r --arg item "${name}" '.[] | select(.item == $item) | [.destination, .sha256] | @tsv' "${artifact_file}")
  done 3<"${plan_file}"
  command+="; sudo rm -rf '${bundle}'"
  # A node that only runs a command receives nothing; an archive of no paths would be the whole tree.
  [[ "${#local_paths[@]}" -gt 0 ]] || return 0
  printf 'staging %s committed paths on %s\n' "${#local_paths[@]}" "${target_node}"
  python3 "${script_dir}/local-build.py" archive --index "${artifact_file}" --commit "${head_commit}" -- "${local_paths[@]}" | \
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

# All of a node's commands in one SSH, in order, each in its own subshell so that one
# command's cd or set does not leak into the next; the first failure stops the node.
run_commands_node() {
  local target_node=$1 command_file=$2 node remote_command script="set -eu; export ISUCON_DEPLOY_RELEASE='${release}'" count=0
  while IFS=$'\t' read -r node remote_command <&3; do
    [[ "${node}" == "${target_node}" ]] || continue
    script+="; (set -eu; ${remote_command})"
    count=$((count + 1))
  done 3<"${command_file}"
  [[ "${count}" -gt 0 ]] || return 0
  "${script_dir}/ssh-node.sh" "${target_node}" "${script}"
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

cleanup_staging() { run_node_stage cleanup || true; }

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
  run_node_stage restore || echo "one or more nodes failed to restore transaction ${release}" >&2
  case "${failed_state}" in
    switching|validating)
      run_node_stage rollback_commands || echo "one or more rollback commands failed for ${release}" >&2
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

mark_stage preflight
run_node_stage preflight
mark_stage staging
run_node_stage staging
mark_stage building
run_node_stage building
mark_stage switching
run_node_stage switching
mark_stage validating
run_node_stage validating

trap - EXIT INT TERM
cleanup_staging
mark_stage complete
printf '%s\n' "${release}" >"${sync_repo_dir}/.local/current-release"
printf '%s\n' "${head_commit}" >"${sync_repo_dir}/.local/current-commit"
printf '%s\n' "${head_commit}" >"${sync_repo_dir}/.local/current-deploy-commit.tmp"
mv "${sync_repo_dir}/.local/current-deploy-commit.tmp" \
  "${sync_repo_dir}/.local/current-deploy-commit"

# rollback用backupは新しいものだけを残す。prune失敗はdeploy成功を取り消さない。
run_node_stage pruning || true
deploy_elapsed=$(($(date +%s) - deploy_started_epoch))
echo "deploy complete: ${release} (${deploy_elapsed}s)"
