#!/usr/bin/env bash
# 全nodeを並列に検査する。nodeごとに1回のSSHで、そのnodeの様子（load、メモリ、disk、失敗したunit、
# 待ち受けport、直近のOOM killとerror、MySQLの実行中のSQL）と、config/sync.jsonのstatus_commandsの
# 結果を返す。応答しないnodeはSTATUS_TIMEOUT_SECONDS（既定20秒）で打ち切って「応答なし」と出す。
# 失敗にするのはstatus_commandsの失敗と応答しないnodeだけで、様子は判断の材料として出す。
# practice-14-seventhでは、メモリを使い切ったDBのnodeに手打ちのsshが120秒固まり、ポートが開くのを
# 待つloopで180秒ずつ待ってから原因に気付いた。
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=sync-lib.sh
source "${script_dir}/sync-lib.sh"
sync_validate

timeout=${STATUS_TIMEOUT_SECONDS:-20}
[[ "${timeout}" =~ ^[1-9][0-9]*$ ]] || { echo "STATUS_TIMEOUT_SECONDS must be a positive integer" >&2; exit 2; }
work=$(mktemp -d)
trap 'rm -rf -- "${work}"' EXIT

# nodeで動かす部分。どの行も5秒で打ち切り（timeoutが無ければそのまま）、sudoは聞き返さない。
read -r -d '' diagnose <<'REMOTE' || true
t() { if command -v timeout >/dev/null 2>&1; then timeout 5 "$@"; else "$@"; fi; }
echo "load     $(cut -d' ' -f1-3 /proc/loadavg) ($(nproc 2>/dev/null) cores), $(t uptime -p 2>/dev/null)"
t free -m 2>/dev/null | awk 'NR==2 {printf "memory   %s of %s MiB used, %s MiB available\n", $3, $2, $7} NR==3 && $2 > 0 {printf "swap     %s of %s MiB used\n", $3, $2}'
t df -h / 2>/dev/null | awk 'NR==2 {print "disk     / " $5 " used, " $4 " free"}'
failed_units=$(t systemctl --failed --no-legend --plain 2>/dev/null | awk '{print $1}' | paste -sd' ' -)
echo "failed   ${failed_units:-none}"
listening=$(t sudo -n ss -ltnpH 2>/dev/null | awk '{name = $6; sub(/^users:\(\("/, "", name); sub(/".*/, "", name); print $4 "(" name ")"}' | sort -u | paste -sd' ' -)
echo "listen   ${listening:-unknown (needs sudo)}"
t sudo -n journalctl -k --since -30min --no-pager -q 2>/dev/null | grep -iE 'out of memory|oom-kill|killed process' | tail -n 2 | cut -c1-200 | sed 's/^/oom      /'
t sudo -n journalctl -p err --since -10min --no-pager -q -o short-iso 2>/dev/null | tail -n 3 | cut -c1-200 | sed 's/^/error    /'
if systemctl is-active -q mysql 2>/dev/null || systemctl is-active -q mariadb 2>/dev/null; then
  t sudo -n mysql -N -e "SELECT CONCAT(TIME, 's ', COALESCE(STATE, ''), ' ', LEFT(REPLACE(COALESCE(INFO, ''), '\n', ' '), 120)) FROM information_schema.PROCESSLIST WHERE COMMAND NOT IN ('Sleep', 'Daemon') AND ID <> CONNECTION_ID() ORDER BY TIME DESC LIMIT 3" 2>/dev/null | sed 's/^/mysql    /'
fi
failed=0
check() {
  output=$(t sh -c "$1" 2>&1)
  code=$?
  output=$(printf '%s' "${output}" | tr '\n' ' ' | cut -c1-160)
  if [ "${code}" -eq 0 ]; then
    echo "check    ok      $1: ${output}"
  else
    echo "check    FAILED  $1 (exit ${code}): ${output}"
    failed=$((failed + 1))
  fi
}
REMOTE

nodes=()
add_node() {
  local known
  for known in ${nodes[@]+"${nodes[@]}"}; do
    [[ "${known}" == "$1" ]] && return 0
  done
  nodes+=("$1")
}
while IFS= read -r node; do add_node "${node}"; done < <(sync_group_nodes application)
while IFS=$'\t' read -r command_group _; do
  while IFS= read -r node; do add_node "${node}"; done < <(sync_group_nodes "${command_group}")
done < <(sync_commands status_commands)

remote_script() {
  local node=$1 command_group command quoted
  printf '%s\n' "${diagnose}"
  while IFS=$'\t' read -r command_group command; do
    sync_group_nodes "${command_group}" | grep -qxF -- "${node}" || continue
    quoted=${command//\'/\'\\\'\'}
    printf "check '%s'\n" "${quoted}"
  done < <(sync_commands status_commands)
  # shellcheck disable=SC2016 # nodeのshellで展開する。
  printf '%s\n' 'exit "$((failed > 0))"'
}

inspect() {
  local node=$1 started pid tick code
  started=$(date +%s)
  # 固まった接続を使い回さないよう、このSSHは新しく張る。
  SSH_CONTROL_PERSIST=0 "${script_dir}/ssh-node.sh" "${node}" "$(remote_script "${node}")" \
    >"${work}/${node}.out" 2>&1 </dev/null &
  pid=$!
  # 打ち切りは0.1秒ごとに確かめる（sleepを残すと、出力を受ける側が打ち切りまで待たされる）。
  for ((tick = 0; tick < timeout * 10; tick++)); do
    kill -0 "${pid}" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "${pid}" 2>/dev/null; then
    kill -TERM "${pid}" 2>/dev/null || true
    touch "${work}/${node}.expired"
  fi
  code=0
  wait "${pid}" || code=$?
  printf '%s %s\n' "${code}" "$(($(date +%s) - started))" >"${work}/${node}.result"
}

for node in ${nodes[@]+"${nodes[@]}"}; do
  inspect "${node}" &
done
wait

failed=0
for node in ${nodes[@]+"${nodes[@]}"}; do
  read -r code seconds <"${work}/${node}.result"
  if [[ -f "${work}/${node}.expired" ]]; then
    printf '== %s: no answer within %ss (out of memory, overloaded or rebooting? look at the instance from the cloud console)\n' \
      "${node}" "${timeout}"
    failed=1
  elif [[ "${code}" -eq 255 ]]; then
    printf '== %s: SSH failed (%ss)\n' "${node}" "${seconds}"
    failed=1
  elif [[ "${code}" -ne 0 ]]; then
    printf '== %s: a status command failed (%ss)\n' "${node}" "${seconds}"
    failed=1
  else
    printf '== %s: ok (%ss)\n' "${node}" "${seconds}"
  fi
  sed 's/^/  /' "${work}/${node}.out"
done
exit "${failed}"
