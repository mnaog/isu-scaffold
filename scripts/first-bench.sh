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
# Run it from the main session, never from a lane, so the run records that conversation.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
hypothesis=${HYPOTHESIS:-初期状態の負荷構造とベンチシナリオを記録する}
mode=${FIRST_BENCH_MODE:-auto}

# shellcheck disable=SC1091
source "${script_dir}/stage-lib.sh"
progress() { python3 "${script_dir}/progress.py" emit first-bench "$@" || true; }

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

progress started "phase1-check, then the baseline"
stage phase1-check "${script_dir}/phase1-check.sh"
progress checked "phase1-check passed"

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
  stage survey-run isuscope survey-run --hypothesis "${hypothesis}"
  progress baseline-ready "analyze with isuscope brief latest; deploying the same commit meanwhile"
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
  stage survey-run isuscope survey-run --hypothesis "${hypothesis}"
  progress baseline-ready "analyze with isuscope brief latest"
fi
progress complete "a pending result needs the sample and isuscope rescore; then set the initialize markers (scripts/benchmark-markers.py)"
