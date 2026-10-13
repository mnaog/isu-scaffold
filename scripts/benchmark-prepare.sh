#!/usr/bin/env bash
set -euo pipefail

# isuscopeの[benchmark] prepare_commandとして、毎回のベンチの前（collectorより前、isuscopeの
# lockの中）に動きます。deployしたcommitとHEADを照合し（check-deployed-commit.sh）、動いているアプリのunitを再起動し、そのprocessがportをlistenするまで
# 待ちます。前のベンチが途中で切ったrequestのtransactionが接続poolに残り、次のinitializeの
# DROP TABLEを止めること（practice-14の初回survey-run）を防ぎます。止めてあるunitは起動しません。
# REQUIRE_ACTIVE=1なら、止まっているunitも失敗にします（make first-benchが配布状態で測る前の確認）。
#
# 前回のベンチと同じ配布物・計測設定（benchmark-identity.py）なら、BENCH_REPEAT="理由"がない限り
# 始めません（何も変わらなかった編集の後に、同じ構成を1run測り直さないため）。
# 再起動は、前回のベンチを処理したprocessがまだ動いているunitだけに行います。systemdの
# InvocationIDで比べ、deployなどで前回のベンチ後に起動し直したprocessは再起動しません。
# PREPARE_CHECK_ONLY=1（make first-benchの事前確認）はどちらの記録も読み書きしません。

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=${REPO_DIR:-$(cd -- "${script_dir}/.." && pwd)}
identity_file=${repo_dir}/.local/last-benchmark-identity
invocation_file=${repo_dir}/.local/benchmark-invocations.json
check_only=$([[ "${PREPARE_CHECK_ONLY:-}" == 1 ]] && echo 1 || echo 0)

# 記録上のcommitとサーバーで動いているcommitがずれたrunを作らないよう、最初に照合する。
"${script_dir}/check-deployed-commit.sh"
identity=
if [[ "${check_only}" != 1 ]]; then
  identity=$(python3 "${script_dir}/benchmark-identity.py")
  if [[ -s "${identity_file}" && "$(cat "${identity_file}")" == "${identity}" ]]; then
    if [[ -z "${BENCH_REPEAT:-}" ]]; then
      echo "前回のベンチと同じ配布物・計測設定です（変更が入っていない可能性）。編集とcommit・deployを確かめてください。" >&2
      echo "意図した再測定（ばらつきの確認など）なら: BENCH_REPEAT=\"理由\" isuscope run --hypothesis \"...\"" >&2
      exit 1
    fi
    echo "repeating the previous benchmark on purpose: ${BENCH_REPEAT}"
  fi
fi
sync=${SYNC_MANIFEST:-${repo_dir}/config/sync.json}
inventory=${ANSIBLE_INVENTORY:-${repo_dir}/.local/ansible-inventory.json}
wait_seconds=${PREPARE_WAIT_SECONDS:-30}
require_active=$([[ "${REQUIRE_ACTIVE:-}" == 1 ]] && echo 1 || echo 0)
command -v jq >/dev/null

record_identity() {
  [[ "${check_only}" == 1 ]] || { mkdir -p "$(dirname -- "${identity_file}")"; printf '%s\n' "${identity}" >"${identity_file}"; }
}

units=$(jq -r '[.items[]? | select(.name == "rust-service") | .remote | split("/") | last] | join(" ")' "${sync}")
if [[ -z "${units}" ]]; then
  echo "no application unit (rust-service item) in ${sync}; nothing to restart"
  record_identity
  exit 0
fi
for unit in ${units}; do
  [[ "${unit}" =~ ^[A-Za-z0-9_.@-]+\.service$ ]] || { echo "invalid unit name in ${sync}: ${unit}" >&2; exit 1; }
done
group=$(jq -r '[.items[] | select(.name == "rust-service") | .node_group] | first' "${sync}")
nodes=$(jq -r --arg group "${group}" '.all.children[$group].hosts // {} | keys | join(" ")' "${inventory}")
case "${wait_seconds}" in ''|*[!0-9]*) echo "PREPARE_WAIT_SECONDS must be an integer" >&2; exit 1 ;; esac

# 再起動はtry-restart（動いているunitだけ）。listenの確認はunitのcgroupの全processで行う
# （MainPIDが子processへ処理を任せる構成でも待てる）。cgroupが読めなければMainPIDを見る。
# servedは前回のベンチを処理したprocessのInvocationID（unit=ID）。今のIDが同じか不明なら再起動する。
remote_script() {
  local served=$1
  cat <<EOF
set -eu
units="${units}"
served="${served}"
for unit in \$units; do
  current=\$(systemctl show -p InvocationID --value "\$unit" 2>/dev/null || true)
  previous=
  for entry in \$served; do
    if [ "\${entry%%=*}" = "\$unit" ]; then previous=\${entry#*=}; fi
  done
  if [ -n "\$current" ] && [ -n "\$previous" ] && [ "\$current" != "\$previous" ]; then
    echo "FRESH \$unit"
  else
    sudo systemctl try-restart "\$unit"
  fi
done
deadline=\$(( \$(date +%s) + ${wait_seconds} ))
while :; do
  ready=1
  for unit in \$units; do
    if ! systemctl is-active --quiet "\$unit"; then
      [ "${require_active}" = 1 ] && ready=0
      continue
    fi
    group=\$(systemctl show -p ControlGroup --value "\$unit")
    pids=\$(cat "/sys/fs/cgroup\${group}/cgroup.procs" 2>/dev/null || systemctl show -p MainPID --value "\$unit")
    listening=\$(sudo ss -ltnpH 2>/dev/null || true)
    found=0
    for pid in \$pids; do
      if printf '%s\n' "\$listening" | grep -q "pid=\$pid,"; then found=1; fi
    done
    [ "\$found" = 1 ] || ready=0
  done
  [ "\$ready" = 1 ] && break
  if [ "\$(date +%s)" -ge "\$deadline" ]; then
    echo "application did not start listening within ${wait_seconds}s: \$units" >&2
    exit 1
  fi
  sleep 0.5
done
for unit in \$units; do echo "SERVING \$unit \$(systemctl show -p InvocationID --value "\$unit" 2>/dev/null || true)"; done
EOF
}

served_for() {
  [[ "${check_only}" != 1 && -s "${invocation_file}" ]] || return 0
  jq -r --arg node "$1" '(.[$node] // {}) | to_entries | map("\(.key)=\(.value)") | join(" ")' "${invocation_file}" 2>/dev/null || true
}

work=$(mktemp -d)
trap 'rm -rf -- "${work}"' EXIT
failed=0
pids=""
for node in ${nodes}; do
  "${script_dir}/ssh-node.sh" "${node}" "$(remote_script "$(served_for "${node}")")" >"${work}/${node}" &
  pids="${pids} $!:${node}"
done
for entry in ${pids}; do
  node=${entry#*:}
  if wait "${entry%%:*}"; then
    fresh=$(awk '$1 == "FRESH" {print $2}' "${work}/${node}" | tr '\n' ' ')
    echo "${node}: ${units} listening${fresh:+ (not restarted, started after the last benchmark: ${fresh% })}"
  else
    echo "${node}: prepare failed" >&2
    failed=1
  fi
done
[[ "${failed}" == 0 ]] || exit 1
if [[ "${check_only}" != 1 ]]; then
  # このベンチを処理するprocess。次のprepareは、これがまだ動いているunitだけを再起動する。
  mkdir -p "$(dirname -- "${invocation_file}")"
  for node in ${nodes}; do
    awk -v node="${node}" '$1 == "SERVING" && $3 != "" {print node "\t" $2 "\t" $3}' "${work}/${node}"
  done | jq -R -s 'split("\n") | map(select(length > 0) | split("\t")) |
    reduce .[] as $row ({}; .[$row[0]][$row[1]] = $row[2])' >"${invocation_file}.tmp"
  mv "${invocation_file}.tmp" "${invocation_file}"
fi
record_identity
