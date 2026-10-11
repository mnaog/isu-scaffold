#!/usr/bin/env bash
set -euo pipefail

# isuscopeの[benchmark] prepare_commandとして、毎回のベンチの前（collectorより前、isuscopeの
# lockの中）に動きます。deployしたcommitとHEADを照合し（check-deployed-commit.sh）、動いているアプリのunitを再起動し、そのprocessがportをlistenするまで
# 待ちます。前のベンチが途中で切ったrequestのtransactionが接続poolに残り、次のinitializeの
# DROP TABLEを止めること（practice-14の初回survey-run）を防ぎます。止めてあるunitは起動しません。

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)

# 記録上のcommitとサーバーで動いているcommitがずれたrunを作らないよう、最初に照合する。
"${script_dir}/check-deployed-commit.sh"
sync=${SYNC_MANIFEST:-${repo_dir}/config/sync.json}
inventory=${ANSIBLE_INVENTORY:-${repo_dir}/.local/ansible-inventory.json}
wait_seconds=${PREPARE_WAIT_SECONDS:-30}
command -v jq >/dev/null

units=$(jq -r '[.items[]? | select(.name == "rust-service") | .remote | split("/") | last] | join(" ")' "${sync}")
if [[ -z "${units}" ]]; then
  echo "no application unit (rust-service item) in ${sync}; nothing to restart"
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
remote=$(cat <<EOF
set -eu
units="${units}"
for unit in \$units; do sudo systemctl try-restart "\$unit"; done
deadline=\$(( \$(date +%s) + ${wait_seconds} ))
while :; do
  ready=1
  for unit in \$units; do
    systemctl is-active --quiet "\$unit" || continue
    group=\$(systemctl show -p ControlGroup --value "\$unit")
    pids=\$(cat "/sys/fs/cgroup\${group}/cgroup.procs" 2>/dev/null || systemctl show -p MainPID --value "\$unit")
    listening=\$(sudo ss -ltnpH 2>/dev/null || true)
    found=0
    for pid in \$pids; do
      if printf '%s\n' "\$listening" | grep -q "pid=\$pid,"; then found=1; fi
    done
    [ "\$found" = 1 ] || ready=0
  done
  [ "\$ready" = 1 ] && exit 0
  if [ "\$(date +%s)" -ge "\$deadline" ]; then
    echo "application did not start listening within ${wait_seconds}s: \$units" >&2
    exit 1
  fi
  sleep 0.5
done
EOF
)

failed=0
pids=""
for node in ${nodes}; do
  "${script_dir}/ssh-node.sh" "${node}" "${remote}" &
  pids="${pids} $!:${node}"
done
for entry in ${pids}; do
  if wait "${entry%%:*}"; then
    echo "${entry#*:}: ${units} restarted and listening"
  else
    echo "${entry#*:}: prepare failed" >&2
    failed=1
  fi
done
exit "${failed}"
