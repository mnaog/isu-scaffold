#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUCON_INTERNAL_OPERATION_LOCK_HELD:-false}" != true ]]; then
  exec "${script_dir}/with-operation-lock.sh" "$0" "$@"
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
"${script_dir}/render-environment.sh"
