#!/usr/bin/env bash
set -euo pipefail

project_root=${ISUSCOPE_PROJECT_ROOT:?ISUSCOPE_PROJECT_ROOT is required}
run_dir=${ISUSCOPE_RUN_DIR:-${project_root}/.local}
headers_file=${BENCHMARK_HTTP_HEADERS_FILE:-}
body_file=${BENCHMARK_HTTP_BODY_FILE:-}
case "${headers_file}" in ''|/*) ;; *) headers_file=${project_root}/${headers_file} ;; esac
case "${body_file}" in ''|/*) ;; *) body_file=${project_root}/${body_file} ;; esac

request_timeout=${BENCHMARK_HTTP_REQUEST_TIMEOUT_SECONDS:-10}
curl_arguments=(--fail --silent --show-error --connect-timeout "${request_timeout}" --max-time "${request_timeout}")
if [[ -n "${headers_file}" ]]; then
  test -f "${headers_file}" || { echo "missing HTTP headers file: ${headers_file}" >&2; exit 1; }
  while IFS= read -r header; do
    case "${header}" in ''|'#'*) continue ;; esac
    curl_arguments+=(-H "${header}")
  done <"${headers_file}"
fi

if [[ "${1:-}" == --probe ]]; then
  probe_url=${BENCHMARK_HTTP_PROBE_URL:-${BENCHMARK_HTTP_START_URL:-}}
  test -n "${probe_url}" || { echo "BENCHMARK_HTTP_PROBE_URL is empty" >&2; exit 1; }
  curl "${curl_arguments[@]}" -X "${BENCHMARK_HTTP_PROBE_METHOD:-GET}" \
    -o /dev/null "${probe_url}"
  echo "HTTP benchmark endpoint is reachable"
  exit 0
fi

mkdir -p "${run_dir}/tmp"
start_response=$(mktemp "${run_dir}/tmp/http-start.XXXXXX")
status_response=$(mktemp "${run_dir}/tmp/http-status.XXXXXX")
cleanup() { rm -f -- "${start_response}" "${status_response}"; }
trap cleanup EXIT

start_arguments=("${curl_arguments[@]}" -X "${BENCHMARK_HTTP_START_METHOD:-POST}")
if [[ -n "${body_file}" ]]; then
  test -f "${body_file}" || { echo "missing HTTP body file: ${body_file}" >&2; exit 1; }
  start_arguments+=(--data-binary "@${body_file}")
fi
curl "${start_arguments[@]}" "${BENCHMARK_HTTP_START_URL:?}" >"${start_response}"
jq -e . "${start_response}" >/dev/null

final_response=${start_response}
if [[ -n "${BENCHMARK_HTTP_STATUS_URL:-}" ]]; then
  benchmark_id=$(jq -r "${BENCHMARK_HTTP_ID_JQ}" "${start_response}")
  [[ "${benchmark_id}" =~ ^[A-Za-z0-9._-]+$ ]] || {
    echo "HTTP start response did not contain a safe benchmark ID" >&2
    exit 1
  }
  status_url=${BENCHMARK_HTTP_STATUS_URL//\{id\}/${benchmark_id}}
  poll=0
  while :; do
    poll=$((poll + 1))
    [[ "${poll}" -le "${BENCHMARK_HTTP_MAX_POLLS:-600}" ]] || {
      echo "HTTP benchmark polling exceeded BENCHMARK_HTTP_MAX_POLLS" >&2
      exit 1
    }
    curl "${curl_arguments[@]}" "${status_url}" >"${status_response}"
    jq -e . "${status_response}" >/dev/null
    if jq -e "${BENCHMARK_HTTP_DONE_JQ}" "${status_response}" >/dev/null; then
      final_response=${status_response}
      break
    fi
    sleep "${BENCHMARK_HTTP_POLL_INTERVAL_SECONDS:-2}"
  done
fi

# 値の取り出しに`jq -e`は使わない。`-e`はfalseやnullを終了statusで表すため、
# FAILした（pass=false）正常な応答が`set -e`でここを落としてしまう。
score=$(jq -r "${BENCHMARK_HTTP_SCORE_JQ}" "${final_response}")
pass=$(jq -r "${BENCHMARK_HTTP_PASS_JQ}" "${final_response}")
message=$(jq -r "${BENCHMARK_HTTP_MESSAGE_JQ}" "${final_response}")
[[ "${score}" =~ ^[0-9]+$ ]] || { echo "HTTP score is not an integer: ${score}" >&2; exit 1; }
[[ "${pass}" == true || "${pass}" == false ]] || {
  echo "HTTP pass is not boolean: ${pass}" >&2
  exit 1
}
jq -nc --argjson score "${score}" --argjson pass "${pass}" --arg message "${message}" \
  '{score:$score,pass:$pass,message:$message}'
