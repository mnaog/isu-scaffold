#!/usr/bin/env bash
set -euo pipefail

# The first benchmark in one explicit command, after the agent has committed the full import.
# It removes the agent's turn between the steps, not the decisions before them: the draft
# review and the import commit stay with the operator.
#
# Before the first deploy the servers run the imported state as distributed. When HEAD still
# has the imported digests (scripts/imported-state.py) and the application unit is running and
# listening on every node, the baseline is taken on that distributed build, without waiting
# for the local Rust build and deploy; deploy follows the benchmark, so the deploy path is
# proven before the first improvement. Otherwise, or with FIRST_BENCH_MODE=deploy, it deploys
# first as before. Each step takes the operation lock itself, so this script holds no lock.
# Once the baseline exists, the scouts start (make scout-start), so their first reports read it
# while the deploy runs; FIRST_BENCH_SCOUT=0 leaves them stopped. A scout that cannot start never
# fails the benchmark.
# Run it from the main session, never from a lane, so the run records that conversation.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
hypothesis=${HYPOTHESIS:-初期状態の負荷構造とベンチシナリオを記録する}
mode=${FIRST_BENCH_MODE:-auto}
scout=${FIRST_BENCH_SCOUT:-1}

# shellcheck disable=SC1091
source "${script_dir}/stage-lib.sh"
progress() { python3 "${script_dir}/progress.py" emit first-bench "$@" || true; }

# The id and state of the newest run, as `isuscope list` shows them ("" when there is none).
latest_run() {
  isuscope list --limit 1 2>/dev/null | python3 -c '
import json, sys
runs = json.load(sys.stdin)["runs"]
row = dict(zip(runs["columns"], runs["rows"][0])) if runs["rows"] else {}
print(row.get("id", ""), row.get("state", ""))' 2>/dev/null || true
}

# isuscope exits 1 for any result that is not a PASS, also for one the adapter left pending
# before the output's shape is known (practice-12-sixth). A pending baseline is the expected
# first result: it is completed later with the saved output and `isuscope rescore`, so the
# deploy after it still runs. A new run in any other state stays a failure.
survey_run() {
  local before code=0 id state
  before=$(latest_run)
  isuscope survey-run --hypothesis "${hypothesis}" || code=$?
  [[ "${code}" -eq 0 ]] && return 0
  read -r id state <<<"$(latest_run)"
  if [[ "${state}" == pending && -n "${id}" && "${id}" != "${before%% *}" ]]; then
    echo "survey-run ${id} is pending: check how its output is read with python3 scripts/benchmark-sample.py ${id}, then --apply --score N --pass true|false"
    return 0
  fi
  return "${code}"
}

# Already running (a restart of make first-bench) or unable to start: say so and go on.
start_scout() {
  if [[ "${scout}" == 0 ]]; then
    progress scout-skipped "FIRST_BENCH_SCOUT=0; start them with make scout-start"
  elif python3 "${script_dir}/scout/main.py" start; then
    progress scout-started "the scouts read the baseline and report 8 minutes after each report; make scout-status"
  else
    progress scout-not-started "see python3 scripts/scout/main.py status; make scout-start"
  fi
}

cd "${repo_dir}"
if [[ -n "${ISUCON_LANE:-}" ]]; then
  echo "lanes do not benchmark; ask the main session to run make first-bench" >&2
  exit 2
fi
case "${mode}" in auto|deploy) ;; *) echo "FIRST_BENCH_MODE must be auto or deploy" >&2; exit 2 ;; esac
if [[ -n "$(git status --porcelain --untracked-files=all -- webapp config)" ]]; then
  echo "commit the full import and configuration first (webapp/ or config/ has changes):" >&2
  git status --short -- webapp config >&2
  exit 1
fi

progress started "bench-check, then the baseline"
stage bench-check "${script_dir}/bench-check.sh"
progress checked "bench-check passed"

distributed=false
marker=.local/current-import-commit
if [[ "${mode}" == auto && ! -s .local/current-deploy-commit ]]; then
  if python3 "${script_dir}/imported-state.py"; then
    git rev-parse HEAD >"${marker}"
    # The check restarts the unit and waits for it to listen, as every benchmark's prepare does.
    if REQUIRE_ACTIVE=1 PREPARE_CHECK_ONLY=1 "${script_dir}/benchmark-prepare.sh"; then
      distributed=true
    else
      rm -f -- "${marker}"
    fi
  fi
  if [[ "${distributed}" != true ]]; then
    progress fallback "the distributed state cannot be benchmarked as is; deploying first"
  fi
fi

if [[ "${distributed}" == true ]]; then
  progress distributed "benchmarking the distributed build at $(git rev-parse --short HEAD) without waiting for build and deploy"
  stage survey-run survey_run
  progress baseline-ready "analyze with isuscope brief latest; deploying the same commit meanwhile"
  start_scout
  # Deploy only what was measured: a change committed during the survey-run (make tare, a
  # cherry-picked lane commit) must get its own run, so it is not shipped here unmeasured.
  if "${script_dir}/check-deployed-commit.sh" >/dev/null 2>&1; then
    stage deploy "${script_dir}/deploy.sh"
    progress deployed "$(git rev-parse --short HEAD) built locally and deployed; later runs use this build"
  else
    progress deploy-skipped "HEAD changed deployed files after the baseline; make deploy with the next isuscope run"
  fi
else
  stage deploy "${script_dir}/deploy.sh"
  progress deployed "$(git rev-parse --short HEAD); starting survey-run"
  stage survey-run survey_run
  progress baseline-ready "analyze with isuscope brief latest"
  start_scout
fi
progress complete "a pending result: python3 scripts/benchmark-sample.py <run>, then --apply"
