#!/usr/bin/env bash
set -euo pipefail

# isuscope benchmark adapter protocol v1
#
# config/benchmark.envの標準adapterは、操作端末、SSH node、HTTP APIのいずれかで
# ベンチを1回だけ実行し、scoreとPASS/FAILをisuscopeへ返します。

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=${ISUSCOPE_PROJECT_ROOT:-$(cd -- "${script_dir}/.." && pwd)}
benchmark_config=${BENCHMARK_CONFIG_FILE:-${project_root}/config/benchmark.env}
benchmark_secrets=${BENCHMARK_SECRETS_FILE:-${project_root}/.local/benchmark-secrets.env}
inventory_path=${ANSIBLE_INVENTORY:-${project_root}/.local/ansible-inventory.json}

load_config() {
  test -f "${benchmark_config}" || { echo "missing ${benchmark_config}" >&2; return 1; }
  set -a
  # shellcheck disable=SC1090
  source "${benchmark_config}"
  if [[ -f "${benchmark_secrets}" ]]; then
    # shellcheck disable=SC1090
    source "${benchmark_secrets}"
  fi
  set +a
}

validate_regex() {
  local label=$1 pattern=$2 status
  set +e
  printf '' | grep -E "${pattern}" >/dev/null 2>&1
  status=$?
  set -e
  if [[ "${status}" -eq 2 ]]; then
    echo "invalid ${label} regex" >&2
    return 1
  fi
}

validate_config() {
  load_config
  case "${BENCHMARK_TRANSPORT:-}" in
    local)
      test -n "${BENCHMARK_COMMAND:-}" || { echo "BENCHMARK_COMMAND is empty" >&2; return 1; }
      test -n "${BENCHMARK_PROBE_COMMAND:-}" || { echo "BENCHMARK_PROBE_COMMAND is empty" >&2; return 1; }
      ;;
    ssh)
      test -n "${BENCHMARK_NODE:-}" || { echo "BENCHMARK_NODE is required for ssh transport" >&2; return 1; }
      test -f "${inventory_path}" || { echo "run make discover first" >&2; return 1; }
      jq -e --arg name "${BENCHMARK_NODE}" \
        '[.all.children[].hosts | keys[]] | index($name) != null' \
        "${inventory_path}" >/dev/null || {
          echo "BENCHMARK_NODE is not present in inventory: ${BENCHMARK_NODE}" >&2
          return 1
        }
      test -n "${BENCHMARK_COMMAND:-}" || { echo "BENCHMARK_COMMAND is empty" >&2; return 1; }
      test -n "${BENCHMARK_PROBE_COMMAND:-}" || { echo "BENCHMARK_PROBE_COMMAND is empty" >&2; return 1; }
      ;;
    http)
      command -v curl >/dev/null
      test -n "${BENCHMARK_HTTP_START_URL:-}" || { echo "BENCHMARK_HTTP_START_URL is empty" >&2; return 1; }
      test -n "${BENCHMARK_HTTP_PROBE_URL:-}" || { echo "BENCHMARK_HTTP_PROBE_URL is empty" >&2; return 1; }
      for number in "${BENCHMARK_HTTP_POLL_INTERVAL_SECONDS:-2}" "${BENCHMARK_HTTP_MAX_POLLS:-600}"; do
        case "${number}" in ''|*[!0-9]*) echo "HTTP poll settings must be non-negative integers" >&2; return 1 ;; esac
      done
      request_timeout=${BENCHMARK_HTTP_REQUEST_TIMEOUT_SECONDS:-10}
      case "${request_timeout}" in ''|*[!0-9]*) echo "HTTP request timeout must be a positive integer" >&2; return 1 ;; esac
      [[ "${request_timeout}" -gt 0 ]] || { echo "HTTP request timeout must be greater than zero" >&2; return 1; }
      for optional_file in "${BENCHMARK_HTTP_HEADERS_FILE:-}" "${BENCHMARK_HTTP_BODY_FILE:-}"; do
        [[ -n "${optional_file}" ]] || continue
        case "${optional_file}" in /*) ;; *) optional_file=${project_root}/${optional_file} ;; esac
        test -f "${optional_file}" || { echo "missing HTTP input file: ${optional_file}" >&2; return 1; }
      done
      for expression in \
        "${BENCHMARK_HTTP_ID_JQ:?}" "${BENCHMARK_HTTP_DONE_JQ:?}" \
        "${BENCHMARK_HTTP_SCORE_JQ:?}" "${BENCHMARK_HTTP_PASS_JQ:?}" \
        "${BENCHMARK_HTTP_MESSAGE_JQ:?}"; do
        jq -n "${expression}" >/dev/null 2>&1 || { echo "invalid HTTP jq expression: ${expression}" >&2; return 1; }
      done
      ;;
    *)
      echo "BENCHMARK_TRANSPORT must be local, ssh, or http" >&2
      return 1
      ;;
  esac
  case "${BENCHMARK_TIMEOUT_SECONDS:-900}" in
    ''|*[!0-9]*) echo "BENCHMARK_TIMEOUT_SECONDS must be a non-negative integer" >&2; return 1 ;;
  esac
  if [[ "${BENCHMARK_TIMEOUT_SECONDS}" -gt 0 ]] &&
    ! command -v timeout >/dev/null 2>&1 &&
    ! command -v gtimeout >/dev/null 2>&1 &&
    ! command -v python3 >/dev/null 2>&1; then
    echo "timeout requires timeout, gtimeout, or python3" >&2
    return 1
  fi
  validate_regex BENCHMARK_SCORE_REGEX "${BENCHMARK_SCORE_REGEX:?}"
  validate_regex BENCHMARK_PASS_REGEX "${BENCHMARK_PASS_REGEX:?}"
  validate_regex BENCHMARK_FAIL_REGEX "${BENCHMARK_FAIL_REGEX:?}"

  sample_file=${BENCHMARK_SAMPLE_FILE:?BENCHMARK_SAMPLE_FILE is required}
  case "${sample_file}" in /*) ;; *) sample_file=${project_root}/${sample_file} ;; esac
  test -f "${sample_file}" || { echo "missing benchmark sample: ${sample_file}" >&2; return 1; }
  sample_score=$(grep -Eo "${BENCHMARK_SCORE_REGEX}" "${sample_file}" | tail -n 1 | grep -Eo '[0-9]+' | tail -n 1 || true)
  test -n "${sample_score}" || { echo "benchmark sample does not contain a score" >&2; return 1; }
  if grep -Eiq "${BENCHMARK_FAIL_REGEX}" "${sample_file}"; then
    sample_pass=false
  elif grep -Eiq "${BENCHMARK_PASS_REGEX}" "${sample_file}"; then
    sample_pass=true
  else
    echo "benchmark sample contains neither final PASS nor FAIL" >&2
    return 1
  fi
}

if [[ "${1:-}" == --check ]]; then
  command -v jq >/dev/null
  validate_config
  echo "benchmark adapter configuration is valid (${BENCHMARK_TRANSPORT})"
  exit 0
fi

if [[ "${1:-}" == --probe ]]; then
  command -v jq >/dev/null
  validate_config
  case "${BENCHMARK_TRANSPORT}" in
    local) bash -c "${BENCHMARK_PROBE_COMMAND}" ;;
    ssh) "${project_root}/scripts/ssh-node.sh" "${BENCHMARK_NODE}" "${BENCHMARK_PROBE_COMMAND}" ;;
    http)
      ISUSCOPE_PROJECT_ROOT="${project_root}" \
        "${project_root}/scripts/benchmark-http.sh" --probe
      ;;
  esac
  echo "benchmark endpoint probe passed (${BENCHMARK_TRANSPORT})"
  exit 0
fi

if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${project_root}/.local/operation.lock" -- "$0" "$@"
fi

command -v jq >/dev/null
validate_config
run_dir=${ISUSCOPE_RUN_DIR:?ISUSCOPE_RUN_DIR is required}
mkdir -p "${run_dir}/tmp" "${project_root}/.local"
raw_output=$(mktemp "${run_dir}/tmp/benchmark-output.XXXXXX")
lock_dir=${project_root}/.local/benchmark.lock
if ! mkdir "${lock_dir}" 2>/dev/null; then
  echo "another benchmark adapter appears to be running: ${lock_dir}" >&2
  exit 1
fi
cleanup() {
  rm -f -- "${raw_output}"
  rmdir "${lock_dir}" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

timeout_prefix=()
if [[ "${BENCHMARK_TIMEOUT_SECONDS}" -gt 0 ]]; then
  if command -v timeout >/dev/null 2>&1; then
    timeout_prefix=(timeout "${BENCHMARK_TIMEOUT_SECONDS}")
  elif command -v gtimeout >/dev/null 2>&1; then
    timeout_prefix=(gtimeout "${BENCHMARK_TIMEOUT_SECONDS}")
  elif command -v python3 >/dev/null 2>&1; then
    timeout_prefix=(python3 "${project_root}/scripts/timeout-command.py" "${BENCHMARK_TIMEOUT_SECONDS}")
  else
    echo "timeout requires timeout, gtimeout, or python3" >&2
    exit 1
  fi
fi

set +e
if [[ "${BENCHMARK_TRANSPORT}" == local ]]; then
  if [[ "${BENCHMARK_TIMEOUT_SECONDS}" -gt 0 ]]; then
    "${timeout_prefix[@]}" bash -c "${BENCHMARK_COMMAND}" 2>&1 | tee "${raw_output}"
  else
    bash -c "${BENCHMARK_COMMAND}" 2>&1 | tee "${raw_output}"
  fi
elif [[ "${BENCHMARK_TRANSPORT}" == ssh ]]; then
  if [[ "${BENCHMARK_TIMEOUT_SECONDS}" -gt 0 ]]; then
    "${timeout_prefix[@]}" "${project_root}/scripts/ssh-node.sh" \
      "${BENCHMARK_NODE}" "${BENCHMARK_COMMAND}" 2>&1 | tee "${raw_output}"
  else
    "${project_root}/scripts/ssh-node.sh" \
      "${BENCHMARK_NODE}" "${BENCHMARK_COMMAND}" 2>&1 | tee "${raw_output}"
  fi
else
  if [[ "${BENCHMARK_TIMEOUT_SECONDS}" -gt 0 ]]; then
    "${timeout_prefix[@]}" env ISUSCOPE_PROJECT_ROOT="${project_root}" ISUSCOPE_RUN_DIR="${run_dir}" \
      "${project_root}/scripts/benchmark-http.sh" 2>&1 | tee "${raw_output}"
  else
    ISUSCOPE_PROJECT_ROOT="${project_root}" ISUSCOPE_RUN_DIR="${run_dir}" \
      "${project_root}/scripts/benchmark-http.sh" 2>&1 | tee "${raw_output}"
  fi
fi
benchmark_exit=${PIPESTATUS[0]}
set -e
if [[ "${benchmark_exit}" -eq 124 ]]; then
  echo "benchmark adapter timed out" >&2
  exit 124
fi

score_match=$(grep -Eo "${BENCHMARK_SCORE_REGEX}" "${raw_output}" | tail -n 1 || true)
score=$(printf '%s\n' "${score_match}" | grep -Eo '[0-9]+' | tail -n 1 || true)
test -n "${score}" || {
  echo "benchmark output did not match BENCHMARK_SCORE_REGEX" >&2
  exit 1
}

if [[ "${benchmark_exit}" -ne 0 ]]; then
  passed=false
elif grep -Eiq "${BENCHMARK_FAIL_REGEX}" "${raw_output}"; then
  passed=false
elif grep -Eiq "${BENCHMARK_PASS_REGEX}" "${raw_output}"; then
  passed=true
else
  passed=false
fi

jq -nc \
  --argjson score "${score}" \
  --argjson pass "${passed}" \
  --arg message "benchmark exit status ${benchmark_exit}" \
  '{type:"isuscope.result", score:$score, pass:$pass, messages:[$message]}'
