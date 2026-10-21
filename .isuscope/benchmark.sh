#!/usr/bin/env bash
set -euo pipefail

# isuscope benchmark adapter protocol v1
#
# config/benchmark.envの標準adapterは、操作端末、SSH node、HTTP APIのいずれかで
# ベンチを1回だけ実行し、scoreとPASS/FAILをisuscopeへ返します。

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=${ISUSCOPE_PROJECT_ROOT:-$(cd -- "${script_dir}/.." && pwd)}
benchmark_config=${BENCHMARK_CONFIG_FILE:-${project_root}/config/benchmark.env}
# Secrets and the inventory live in the root repository's .local only. A worktree (the portal
# session builds the connection in one) checks the adapter against those, as progress.log does.
local_root=${project_root}
common_dir=$(git -C "${project_root}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
if [[ -n "${common_dir}" && -d "$(dirname -- "${common_dir}")/.local" ]]; then
  local_root=$(dirname -- "${common_dir}")
fi
benchmark_secrets=${BENCHMARK_SECRETS_FILE:-${local_root}/.local/benchmark-secrets.env}
inventory_path=${ANSIBLE_INVENTORY:-${local_root}/.local/ansible-inventory.json}

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
  python3 "${project_root}/scripts/benchmark-contract.py" --root "${project_root}" >&2
  # isuscope passes its [benchmark] timeout_seconds and stops the run itself only after a
  # grace period, so the adapter's own wall-clock limit follows it. The contract above checks
  # the reviewed settings; this only bounds how long the adapter waits.
  if [[ -n "${ISUSCOPE_BENCHMARK_TIMEOUT_SECONDS:-}" ]]; then
    BENCHMARK_TIMEOUT_SECONDS=${ISUSCOPE_BENCHMARK_TIMEOUT_SECONDS}
  fi
  # An empty BENCHMARK_NODE means the only node of the inventory's benchmark group: discover names
  # nodes per provider (such as <stack>-bench), so a fixed default does not match.
  if [[ "${BENCHMARK_TRANSPORT:-}" == ssh && -z "${BENCHMARK_NODE:-}" && -f "${inventory_path}" ]]; then
    BENCHMARK_NODE=$(jq -r '(.all.children.benchmark.hosts // {}) | keys | if length == 1 then .[0] else empty end' \
      "${inventory_path}")
  fi
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

# 運営向けの行（`config/isuscope-policy.json`の`operator_line_pattern`）はscore・合否に使いません。
exclude_pattern() {
  if [[ -n "${BENCHMARK_EXCLUDE_REGEX:-}" ]]; then
    printf '%s\n' "${BENCHMARK_EXCLUDE_REGEX}"
  elif [[ -f "${project_root}/config/isuscope-policy.json" ]]; then
    jq -r '.operator_line_pattern // empty' "${project_root}/config/isuscope-policy.json"
  fi
}

participant_lines() {
  local pattern
  pattern=$(exclude_pattern)
  if [[ -n "${pattern}" ]]; then
    grep -Ev -- "${pattern}" "$1" || true
  else
    cat -- "$1"
  fi
}

# scoreは最後に一致した行の最後の数値、合否はFAILを優先して最終結果の行から決めます。
# 使った行を残し、別の行（タグ中の数字など）を拾ったときに記録から気付けるようにします。
extract_result() {
  local lines
  lines=$(participant_lines "$1")
  score_line=$(printf '%s\n' "${lines}" | grep -E -- "${BENCHMARK_SCORE_REGEX}" | tail -n 1 || true)
  score=$(printf '%s\n' "${score_line}" | grep -Eo -- "${BENCHMARK_SCORE_REGEX}" | tail -n 1 | grep -Eo '[0-9]+' | tail -n 1 || true)
  result_line=$(printf '%s\n' "${lines}" | grep -Ei -- "${BENCHMARK_FAIL_REGEX}" | tail -n 1 || true)
  if [[ -n "${result_line}" ]]; then
    result=false
  else
    result_line=$(printf '%s\n' "${lines}" | grep -Ei -- "${BENCHMARK_PASS_REGEX}" | tail -n 1 || true)
    if [[ -n "${result_line}" ]]; then result=true; else result=none; fi
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
      test -n "${BENCHMARK_NODE:-}" || {
        echo "BENCHMARK_NODE is empty and the inventory's benchmark group does not have exactly one node; set it in config/benchmark.env" >&2
        return 1
      }
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
  validate_regex "operator line" "$(exclude_pattern)"

  check_sample
}

# 実出力で人間が読んだscoreと合否を宣言し、regexが同じ値を取り出すことを確かめます。
# 存在するだけでは、タグや途中経過の数字を拾っても検査を通ってしまうためです。
# scoreと合否の両方が未記入なら、まだ実出力を見ていない初回として扱い（sample_verified=false）、
# runは判定を保留します。片方だけの記入や、読み取りの食い違いは設定の誤りです。
check_sample() {
  sample_verified=false
  if [[ -z "${BENCHMARK_SAMPLE_SCORE:-}" && -z "${BENCHMARK_SAMPLE_PASS:-}" ]]; then
    return 0
  fi
  sample_file=${BENCHMARK_SAMPLE_FILE:?BENCHMARK_SAMPLE_FILE is required}
  case "${sample_file}" in /*) ;; *) sample_file=${project_root}/${sample_file} ;; esac
  test -f "${sample_file}" || { echo "missing benchmark sample: ${sample_file}" >&2; return 1; }
  case "${BENCHMARK_SAMPLE_SCORE:-}" in
    ''|*[!0-9]*) echo "BENCHMARK_SAMPLE_SCORE must be the final score read from the sample" >&2; return 1 ;;
  esac
  case "${BENCHMARK_SAMPLE_PASS:-}" in
    true|false) ;;
    *) echo "BENCHMARK_SAMPLE_PASS must be true or false as read from the sample" >&2; return 1 ;;
  esac
  extract_result "${sample_file}"
  test -n "${score}" || { echo "benchmark sample does not contain a score" >&2; return 1; }
  if [[ "${score}" != "${BENCHMARK_SAMPLE_SCORE}" ]]; then
    echo "BENCHMARK_SCORE_REGEX extracted ${score} from the sample, expected ${BENCHMARK_SAMPLE_SCORE}: ${score_line}" >&2
    return 1
  fi
  if [[ "${result}" == none ]]; then
    echo "benchmark sample contains neither final PASS nor FAIL" >&2
    return 1
  fi
  if [[ "${result}" != "${BENCHMARK_SAMPLE_PASS}" ]]; then
    echo "PASS/FAIL regex judged the sample as pass=${result}, expected ${BENCHMARK_SAMPLE_PASS}: ${result_line}" >&2
    return 1
  fi
  sample_verified=true
}

PENDING_HINT='run python3 scripts/benchmark-sample.py <run> to see how its saved output is read, then --apply --score N --pass true|false with the values you read (writes the sample, reseals the contract, runs --check and isuscope rescore)'

# 出力からscoreと合否を読み、isuscope protocolの結果を1行出します。
emit_result() {
  local output=$1 exit_status=$2
  shift 2
  extract_result "${output}"
  test -n "${score}" || {
    echo "benchmark output did not match BENCHMARK_SCORE_REGEX (exit status ${exit_status})" >&2
    return 1
  }
  local messages=("benchmark exit status ${exit_status}" "score line: ${score_line}")
  if [[ "${result}" == none ]]; then
    # 途中で切れた出力は途中経過のscoreしか持たないため、PASSにしません。
    messages+=("no final PASS/FAIL line matched")
  else
    messages+=("result line: ${result_line}")
  fi
  if [[ "${exit_status}" -ne 0 || "${result}" != true ]]; then
    passed=false
  else
    passed=true
  fi
  jq -nc \
    --argjson score "${score}" \
    --argjson pass "${passed}" \
    '{type:"isuscope.result", score:$score, pass:$pass, messages:$ARGS.positional}' \
    --args "${messages[@]}" "$@"
}

# 保存したベンチ出力（平文）を、今のregexでどう読むかだけを出します（scripts/benchmark-sample.py）。
# regexを直している途中はcontractと食い違うので、contractもsampleも確かめません。ベンチも動かしません。
if [[ "${1:-}" == --read ]]; then
  command -v jq >/dev/null
  input=${2:?usage: .isuscope/benchmark.sh --read OUTPUT_FILE}
  set -a
  # shellcheck disable=SC1090
  source "${benchmark_config}"
  set +a
  validate_regex BENCHMARK_SCORE_REGEX "${BENCHMARK_SCORE_REGEX:?}"
  validate_regex BENCHMARK_PASS_REGEX "${BENCHMARK_PASS_REGEX:?}"
  validate_regex BENCHMARK_FAIL_REGEX "${BENCHMARK_FAIL_REGEX:?}"
  extract_result "${input}"
  jq -nc --arg score "${score}" --arg result "${result}" --arg score_line "${score_line}" --arg result_line "${result_line}" \
    '{score: (if $score == "" then null else ($score | tonumber) end), pass: ({"true": true, "false": false}[$result]),
      score_line: $score_line, result_line: $result_line}'
  exit 0
fi

if [[ "${1:-}" == --check ]]; then
  command -v jq >/dev/null
  validate_config
  if [[ "${sample_verified}" != true ]]; then
    echo "WARN: score and PASS/FAIL patterns are not verified against a real output yet; the first run is recorded as pending: ${PENDING_HINT}" >&2
  fi
  echo "benchmark adapter configuration is valid (${BENCHMARK_TRANSPORT})"
  exit 0
fi

# 判定を保留したrunの保存済み出力を、確かめた読み取りで読み直します（isuscope rescore）。
if [[ "${1:-}" == --extract ]]; then
  command -v jq >/dev/null
  input=${2:?usage: .isuscope/benchmark.sh --extract BENCHMARK_STDOUT}
  validate_config
  [[ "${sample_verified}" == true ]] || {
    echo "the result patterns are not verified yet: ${PENDING_HINT}" >&2
    exit 1
  }
  saved=$(mktemp)
  output=$(mktemp)
  trap 'rm -f -- "${saved}" "${output}"' EXIT
  case "${input}" in
    *.zst) zstd -dcq -- "${input}" >"${saved}" ;;
    *) cat -- "${input}" >"${saved}" ;;
  esac
  # 保留したときの結果行はベンチの出力ではない。そこに残したベンチの終了コードだけを使う。
  status_line=$(grep -F '"type":"isuscope.result"' "${saved}" | tail -n 1 |
    jq -r '.messages[]? | select(startswith("benchmark exit status "))' 2>/dev/null | head -n 1 || true)
  benchmark_exit=${status_line#benchmark exit status }
  [[ "${benchmark_exit}" =~ ^[0-9]+$ ]] || benchmark_exit=0
  grep -vF '"type":"isuscope.' "${saved}" >"${output}" || true
  emit_result "${output}" "${benchmark_exit}" "read again from the saved output by --extract"
  exit $?
fi

if [[ "${1:-}" == --probe ]]; then
  command -v jq >/dev/null
  validate_config
  case "${BENCHMARK_TRANSPORT}" in
    local) bash -c "${BENCHMARK_PROBE_COMMAND}" ;;
    ssh) ISUCON_ALLOW_BENCHMARK_NODE=1 "${project_root}/scripts/ssh-node.sh" "${BENCHMARK_NODE}" "${BENCHMARK_PROBE_COMMAND}" ;;
    http)
      ISUSCOPE_PROJECT_ROOT="${project_root}" \
        "${project_root}/scripts/benchmark-http.sh" --probe
      ;;
  esac
  echo "benchmark endpoint probe passed (${BENCHMARK_TRANSPORT})"
  exit 0
fi

# isuscopeのcleanup_command。isuscopeが期限切れ・出力の途絶・signalでベンチを止めたときに動き、
# ベンチnodeに残ったベンチ（run時に記録したprocess group）を止め、持ち主の消えたadapterのlockを外す。
# ベンチnodeへ触れてよいのはこのadapterだけなので、後始末もここで行う。
remote_pid_file=/tmp/isuscope-benchmark.pid
if [[ "${1:-}" == --cleanup ]]; then
  load_config
  if [[ "${BENCHMARK_TRANSPORT:-}" == ssh && -n "${BENCHMARK_NODE:-}" ]]; then
    ISUCON_ALLOW_BENCHMARK_NODE=1 "${project_root}/scripts/ssh-node.sh" "${BENCHMARK_NODE}" \
      "pid=\$(cat ${remote_pid_file} 2>/dev/null) || exit 0; for signal in TERM KILL; do sudo -n kill -\$signal -- -\$pid 2>/dev/null || kill -\$signal -- -\$pid 2>/dev/null || true; sleep 5; done; rm -f ${remote_pid_file}" ||
      echo "cleanup: could not reach ${BENCHMARK_NODE}" >&2
  fi
  stale_lock=${project_root}/.local/benchmark.lock
  if [[ -d "${stale_lock}" ]]; then
    owner=$(cat "${stale_lock}/pid" 2>/dev/null || true)
    if [[ -z "${owner}" ]] || ! kill -0 "${owner}" 2>/dev/null; then
      rm -rf -- "${stale_lock}"
      echo "cleanup: removed the adapter lock left by a stopped benchmark"
    fi
  fi
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
  echo "another benchmark adapter appears to be running: ${lock_dir} (after a killed run: .isuscope/benchmark.sh --cleanup)" >&2
  exit 1
fi
printf '%s\n' "$$" >"${lock_dir}/pid"
baseline_file=${project_root}/.local/benchmark-baseline-seconds
run_pid_file=${project_root}/.local/benchmark-run.pid
slow_watch_pid=
cleanup() {
  stop_slow_watch
  rm -f -- "${raw_output}" "${run_pid_file}"
  rm -rf -- "${lock_dir}" 2>/dev/null || true
}

# The isuscope process that started this adapter. scripts/benchmark-stop.sh sends it SIGTERM, so
# isuscope records the run as interrupted and runs cleanup_command, as for its own deadline.
record_run_pid() {
  local pid=$PPID args
  while [[ "${pid}" -gt 1 ]]; do
    args=$(ps -o args= -p "${pid}" 2>/dev/null) || return 0
    if [[ "${args}" =~ isuscope[[:space:]]+(run|survey-run)([[:space:]]|$) ]]; then
      printf '%s\n' "${pid}" >"${run_pid_file}"
      return 0
    fi
    pid=$(ps -o ppid= -p "${pid}" 2>/dev/null | tr -d ' ') || return 0
    [[ -n "${pid}" ]] || return 0
  done
}

# A benchmark that runs past BENCHMARK_SLOW_FACTOR (default 2) times the baseline gets one line in
# .local/progress.log, and the agent decides whether to stop it. Without it a hung run is only
# stopped at the hard deadline (BENCHMARK_TIMEOUT_SECONDS), which stays as the last resort. The watcher holds none of the adapter's pipes, so isuscope never waits on it.
start_slow_watch() {
  local baseline factor=${BENCHMARK_SLOW_FACTOR:-2} limit
  [[ -f "${baseline_file}" ]] || return 0
  baseline=$(tr -d '[:space:]' <"${baseline_file}")
  [[ "${baseline}" =~ ^[0-9]+$ && "${baseline}" -gt 0 && "${factor}" =~ ^[0-9]+$ && "${factor}" -gt 0 ]] || return 0
  limit=$((baseline * factor))
  (
    exec </dev/null >/dev/null 2>&1
    sleep "${limit}"
    python3 "${project_root}/scripts/progress.py" emit benchmark slow \
      "running for ${limit}s, ${factor}x the ${baseline}s baseline. Check the app and nginx error logs" \
      "and node load; stop it with scripts/benchmark-stop.sh, or let it run" \
      "(isuscope stops it at BENCHMARK_TIMEOUT_SECONDS=${BENCHMARK_TIMEOUT_SECONDS})" || true
  ) &
  slow_watch_pid=$!
}

stop_slow_watch() {
  [[ -n "${slow_watch_pid}" ]] || return 0
  pkill -P "${slow_watch_pid}" 2>/dev/null || true
  kill "${slow_watch_pid}" 2>/dev/null || true
  slow_watch_pid=
}

# The first benchmark that ends on its own with output is the baseline the later runs compare with.
record_baseline() {
  local status=$1 seconds=$2
  [[ -f "${baseline_file}" || "${status}" -ne 0 || "${seconds}" -le 0 ]] && return 0
  grep -q '[^[:space:]]' "${raw_output}" || return 0
  printf '%s\n' "${seconds}" >"${baseline_file}"
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

record_run_pid
start_slow_watch
benchmark_started=$(date +%s)
set +e
if [[ "${BENCHMARK_TRANSPORT}" == local ]]; then
  if [[ "${BENCHMARK_TIMEOUT_SECONDS}" -gt 0 ]]; then
    "${timeout_prefix[@]}" bash -c "${BENCHMARK_COMMAND}" 2>&1 | tee "${raw_output}"
  else
    bash -c "${BENCHMARK_COMMAND}" 2>&1 | tee "${raw_output}"
  fi
elif [[ "${BENCHMARK_TRANSPORT}" == ssh ]]; then
  # scripts/ssh-node.shはこの変数がないとベンチnodeへの接続を断る。
  export ISUCON_ALLOW_BENCHMARK_NODE=1
  if [[ "${BENCHMARK_TIMEOUT_SECONDS}" -gt 0 ]]; then
    # ローカルのSSHを止めても、pty無しのremoteのベンチは走り続け、次のrunと重なり得る。
    # ベンチnode側でも同じ時間で止め（TERMの10秒後にKILL）、ローカルはその後の30秒を待つ。
    # sshdが起動したshellはprocess groupの先頭なので、そのPIDを残せば--cleanupが配下ごと止められる。
    remote_command="echo \$\$ > ${remote_pid_file}; exec timeout -k 10 ${BENCHMARK_TIMEOUT_SECONDS} sh -c $(python3 -c 'import shlex, sys; print(shlex.quote(sys.argv[1]))' "${BENCHMARK_COMMAND}")"
    timeout_prefix[${#timeout_prefix[@]}-1]=$((BENCHMARK_TIMEOUT_SECONDS + 30))
    "${timeout_prefix[@]}" "${project_root}/scripts/ssh-node.sh" \
      "${BENCHMARK_NODE}" "${remote_command}" 2>&1 | tee "${raw_output}"
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
stop_slow_watch
record_baseline "${benchmark_exit}" $(($(date +%s) - benchmark_started))
if [[ "${benchmark_exit}" -eq 124 ]]; then
  echo "benchmark adapter timed out" >&2
  exit 124
fi

if [[ "${sample_verified}" != true ]]; then
  # 初回は読み取りを確かめていないので判定を保留し、出力を残して後から確定する。
  # 何も出力しなかったベンチは保留にせず、adapterの失敗として止める。
  grep -q '[^[:space:]]' "${raw_output}" || {
    echo "benchmark produced no output (exit status ${benchmark_exit})" >&2
    exit 1
  }
  extract_result "${raw_output}"
  hints=()
  [[ -n "${score_line}" ]] && hints+=("unverified score line: ${score_line}")
  [[ "${result}" != none ]] && hints+=("unverified result line: ${result_line}")
  jq -nc '{type:"isuscope.result", pending:true, messages:$ARGS.positional}' \
    --args "benchmark exit status ${benchmark_exit}" "result pending: ${PENDING_HINT}" ${hints[@]+"${hints[@]}"}
  exit 0
fi

emit_result "${raw_output}" "${benchmark_exit}"
