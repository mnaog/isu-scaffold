#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi

language=${1:-}
application_path=${2:-}
test -n "${language}" || { echo "LANGUAGE=<name> is required" >&2; exit 2; }

repo_dir=$(cd -- "${script_dir}/.." && pwd)
"${script_dir}/discover.sh"
inventory=${ANSIBLE_INVENTORY:-${repo_dir}/.local/ansible-inventory.json}
source_node=${CODE_SOURCE_NODE:-$(jq -r '.all.children.application.hosts | keys[0] // empty' "${inventory}")}
test -n "${source_node}" || { echo "no application source node" >&2; exit 2; }
SSH_ONLY_NODE="${source_node}" "${script_dir}/bootstrap-ssh.sh"
CODE_SOURCE_NODE="${source_node}" "${script_dir}/quick-import-code.sh" "${language}" "${application_path:-webapp/${language}}"
