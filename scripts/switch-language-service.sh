#!/usr/bin/env bash
set -euo pipefail

# Applies the reviewed switch to the Go implementation from .local/service-switch.json
# (written by configure-draft.py, installed by configure-apply.sh): on each listed node,
# stop and disable the other language's unit, then enable and start the Go unit.
# Without a plan there is nothing to switch.

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
plan=${SERVICE_SWITCH_FILE:-${repo_dir}/.local/service-switch.json}
test -f "${plan}" || exit 0
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${repo_dir}/.local/operation.lock" -- "$0" "$@"
fi
command -v jq >/dev/null

start=$(jq -r '.start' "${plan}")
stop=$(jq -r '.stop | join(" ")' "${plan}")
nodes=$(jq -r '.nodes | join(" ")' "${plan}")
for unit in ${start} ${stop}; do
  [[ "${unit}" =~ ^[A-Za-z0-9_.@-]+\.service$ ]] || { echo "invalid unit name in ${plan}: ${unit}" >&2; exit 1; }
done

remote="set -e"
if [[ -n "${stop}" ]]; then
  remote="${remote}; sudo systemctl disable --now ${stop}"
fi
remote="${remote}; sudo systemctl enable --now ${start}; sudo systemctl is-active --quiet ${start}"

failed=0
for node in ${nodes}; do
  if "${script_dir}/ssh-node.sh" "${node}" "${remote}"; then
    echo "${node}: ${start} is running${stop:+ (stopped ${stop})}"
  else
    echo "${node}: switching to ${start} failed" >&2
    failed=1
  fi
done
exit "${failed}"
