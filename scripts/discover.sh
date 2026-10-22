#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi
environment_file=${ISUCON_ENV_FILE:-${repo_dir}/.local/environment.env}

test -f "${environment_file}" || {
  echo "missing ${environment_file}; copy config/environment.example.env and fill it" >&2
  exit 1
}
# shellcheck disable=SC1090
source "${environment_file}"

case "${DISCOVERY_PROVIDER:-}" in
  aws-cloudformation) "${script_dir}/discover-aws.sh" ;;
  static) "${script_dir}/discover-static.sh" ;;
  *)
    echo "DISCOVERY_PROVIDER must be aws-cloudformation or static" >&2
    exit 2
    ;;
esac

"${script_dir}/apply-node-overrides.sh"

# 型の違うnodeが混ざるのにベンチの台が1台も無いなら、そこで止める。片方の型が運営のベンチの台
# （ネットワーク特化型など）で、名前がappの正規表現に合っていると、importやdeployの対象にしてしまう。
nodes_file=${repo_dir}/.local/discovered-nodes.json
if [[ "${DISCOVERY_ACCEPT_MIXED_TYPES:-}" != 1 ]] && mixed=$(jq -er '
  [.[] | select(.group == "application" and (.instance_type // "") != "")] as $application |
  ($application | group_by(.instance_type)) as $types |
  if ($types | length) > 1 and ([.[] | select(.group == "benchmark")] | length) == 0 then
    $types[] | "  \(.[0].instance_type): \(map(.name) | join(" "))"
  else empty end
' "${nodes_file}"); then
  cat >&2 <<EOF
application nodes run different instance types, and no node is a benchmark node:
${mixed}
If some of them are the organizer's benchmark nodes (check the manual and the portal), do not import
or deploy to them: mark them in .local/node-overrides.json, e.g.
  [{"name": "<node>", "group": "benchmark", "roles": ["benchmark"]}]
or set DISCOVERY_BENCH_NAME_REGEX in .local/environment.env to match their names.
If all of them are yours, set DISCOVERY_ACCEPT_MIXED_TYPES=1 in .local/environment.env.
Then run make discover again.
EOF
  exit 1
fi

"${script_dir}/render-environment.sh"
