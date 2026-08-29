#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
local_dir=${repo_dir}/.local
environment_file=${ISUCON_ENV_FILE:-${local_dir}/environment.env}

command -v jq >/dev/null
test -f "${environment_file}"
# shellcheck disable=SC1090
source "${environment_file}"

nodes_file=${STATIC_NODES_FILE:-.local/nodes.json}
ssh_user=${SSH_USER:?SSH_USER is required}
case "${nodes_file}" in /*) ;; *) nodes_file=${repo_dir}/${nodes_file} ;; esac
test -f "${nodes_file}" || {
  echo "missing ${nodes_file}; copy config/nodes.example.json and edit it" >&2
  exit 1
}

mkdir -p "${local_dir}"
temporary=$(mktemp "${local_dir}/discovered-nodes.XXXXXX")
trap 'rm -f -- "${temporary}"' EXIT
jq --arg default_user "${ssh_user}" '
  map({
    name,
    display_name: (.display_name // .name),
    host,
    group: (.group // "application"),
    roles: (.roles // (if (.group // "application") == "benchmark" then ["benchmark"] else ["app"] end)),
    user: (.user // $default_user),
    provider: "static",
    state: (.state // "running")
  }) | sort_by(.name)
' "${nodes_file}" >"${temporary}"
mv -- "${temporary}" "${local_dir}/discovered-nodes.json"
trap - EXIT
