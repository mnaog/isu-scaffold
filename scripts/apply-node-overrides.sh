#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
local_dir=${repo_dir}/.local
environment_file=${ISUCON_ENV_FILE:-${local_dir}/environment.env}
nodes_file=${local_dir}/discovered-nodes.json

# shellcheck disable=SC1090
source "${environment_file}"
overrides_file=${NODE_OVERRIDES_FILE:-.local/node-overrides.json}
case "${overrides_file}" in /*) ;; *) overrides_file=${repo_dir}/${overrides_file} ;; esac
test -f "${overrides_file}" || exit 0

temporary=$(mktemp "${nodes_file}.XXXXXX")
trap 'rm -f -- "${temporary}"' EXIT
jq --slurpfile overrides "${overrides_file}" '
  ($overrides[0] | map({key:.name, value:.}) | from_entries) as $by_name |
  map(. as $node | ($by_name[$node.name] // {}) as $override |
    . + ($override | del(.name)))
' "${nodes_file}" >"${temporary}"
mv -- "${temporary}" "${nodes_file}"
trap - EXIT
