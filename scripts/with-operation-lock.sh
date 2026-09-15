#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
lock_dir=${repo_dir}/.local/operation.lock
owner_file=${lock_dir}/owner

[[ $# -gt 0 ]] || { echo "usage: $0 COMMAND [ARGUMENT ...]" >&2; exit 2; }

if [[ "${ISUCON_INTERNAL_OPERATION_LOCK_HELD:-false}" == true ]]; then
  exec "$@"
fi

mkdir -p "${repo_dir}/.local"
if ! mkdir "${lock_dir}" 2>/dev/null; then
  lock_pid=
  if [[ -f "${owner_file}" ]]; then
    lock_pid=$(sed -n 's/^pid=//p' "${owner_file}" | head -n 1)
  fi
  if [[ "${lock_pid}" =~ ^[0-9]+$ ]] && ! kill -0 "${lock_pid}" 2>/dev/null; then
    rm -f -- "${owner_file}"
    rmdir "${lock_dir}" 2>/dev/null || true
  fi
  if ! mkdir "${lock_dir}" 2>/dev/null; then
    echo "another modifying operation is running: ${lock_dir}" >&2
    if [[ -f "${owner_file}" ]]; then
      sed 's/^/  /' "${owner_file}" >&2
    fi
    exit 75
  fi
fi

printf 'pid=%s\nstarted_at=%s\noperation=%s\n' \
  "$$" "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$(basename -- "$1")" >"${owner_file}"

release_lock() {
  if [[ -f "${owner_file}" ]] && grep -qx "pid=$$" "${owner_file}"; then
    rm -f -- "${owner_file}"
    rmdir "${lock_dir}" 2>/dev/null || true
  fi
}
trap release_lock EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

export ISUCON_INTERNAL_OPERATION_LOCK_HELD=true
timing_file=${repo_dir}/.local/timing-$(date -u +%Y%m%dT%H%M%SZ)-$$.tsv
started=$SECONDS
result=0
"$@" || result=$?
printf '%s\t%s\t%s\n' "$(basename -- "$1")" "$((SECONDS-started))" "${result}" >"${timing_file}"
echo "operation timing (step, seconds, exit): ${timing_file}" >&2
exit "${result}"
