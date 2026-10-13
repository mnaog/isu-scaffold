#!/usr/bin/env bash
set -euo pipefail

# The first benchmark in one explicit command, after the agent has committed the full import:
# phase1-check (which also overlaps the initial build), deploy, then isuscope survey-run.
# It removes the agent's turn between the steps, not the decisions before them: the draft
# review and the import commit stay with the operator. Each step takes the operation lock
# itself (survey-run takes it from the isuscope config), so this script holds no lock.
# Run it from the main session, never from a lane, so the run records that conversation.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
hypothesis=${HYPOTHESIS:-初期状態の負荷構造とベンチシナリオを記録する}

# shellcheck disable=SC1091
source "${script_dir}/stage-lib.sh"
progress() { python3 "${script_dir}/progress.py" emit first-bench "$@" || true; }

cd "${repo_dir}"
if [[ -n "${ISUCON_LANE:-}" ]]; then
  echo "lanes do not benchmark; ask the main session to run make first-bench" >&2
  exit 2
fi
if [[ -n "$(git status --porcelain --untracked-files=all -- webapp config)" ]]; then
  echo "commit the full import and configuration first (webapp/ or config/ has changes):" >&2
  git status --short -- webapp config >&2
  exit 1
fi

progress started "phase1-check, deploy, survey-run"
stage phase1-check "${script_dir}/phase1-check.sh"
progress checked "phase1-check passed; deploying"
stage deploy "${script_dir}/deploy.sh"
progress deployed "$(git rev-parse --short HEAD); starting survey-run"
stage survey-run isuscope survey-run --hypothesis "${hypothesis}"
progress complete "survey-run finished: isuscope brief latest (a pending result needs the sample and isuscope rescore)"
