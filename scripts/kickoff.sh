#!/usr/bin/env bash
set -euo pipefail

# One entry point for the first hour: early code import, the asynchronous build and the AI
# lanes, then full setup up to a reviewed configuration draft. It stops before anything is
# applied; `CONFIRM_DRAFT=true make kickoff-apply` continues after the draft review.
# Milestones go to .local/progress.log, so the agent can run this in the background.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${repo_dir}/.local/operation.lock" -- "$0" "$@"
fi

# shellcheck disable=SC1091
source "${script_dir}/application-policy.sh"
# shellcheck disable=SC1091
source "${script_dir}/stage-lib.sh"
language=${APPLICATION_LANGUAGE:?set APPLICATION_LANGUAGE in config/application.env}
application_path=${APPLICATION_PATH:?set APPLICATION_PATH in config/application.env}
schema_path=${CODE_SCHEMA_PATH:-webapp/sql}
inventory=${ANSIBLE_INVENTORY:-${repo_dir}/.local/ansible-inventory.json}
code_paths=(config/application.env "${application_path}" "${schema_path}")

progress() { python3 "${script_dir}/progress.py" emit kickoff "$@" || true; }
bootstrap_env=()

cd "${repo_dir}"
progress started "make kickoff"
# Benchmarks are linked to the conversation in this repo's docs/agent-history; a session started
# elsewhere would only be refused at bench-check, minutes later.
stage agent-session python3 "${script_dir}/check-agent-session.py"
if git ls-files --error-unmatch -- "${application_path}" >/dev/null 2>&1 &&
  [[ -z "$(git status --porcelain --untracked-files=all -- "${code_paths[@]}")" ]]; then
  echo "== code: ${application_path} is already imported and committed; skipping early import"
else
  echo "== code: early import of ${application_path} and ${schema_path}"
  stage discover "${script_dir}/discover.sh"
  source_node=${CODE_SOURCE_NODE:-$(jq -r '.all.children.application.hosts | keys[0] // empty' "${inventory}")}
  test -n "${source_node}" || { echo "no application source node" >&2; exit 2; }
  stage source-ssh env SSH_ONLY_NODE="${source_node}" "${script_dir}/bootstrap-ssh.sh"
  # The other nodes' SSH overlaps the code transfer; bootstrap then reuses this run's discovery
  # and, when every node answered, skips its own SSH round. This is not a stage: a failure
  # here is not final, and bootstrap's SSH stage retries and reports it.
  other_ssh_log=${repo_dir}/.local/kickoff-other-ssh.log
  SSH_EXCEPT_NODE="${source_node}" "${script_dir}/bootstrap-ssh.sh" >"${other_ssh_log}" 2>&1 &
  other_ssh_pid=$!
  stage early-import env CODE_SOURCE_NODE="${source_node}" "${script_dir}/quick-import-code.sh" "${language}" "${application_path}"
  bootstrap_env=(BOOTSTRAP_SKIP_DISCOVER=1)
  if wait "${other_ssh_pid}"; then
    bootstrap_env+=(BOOTSTRAP_SKIP_SSH=1)
  else
    echo "SSH to the other nodes failed (${other_ssh_log}); bootstrap retries it" >&2
  fi
  existing=()
  for path in "${code_paths[@]}"; do
    [[ -e "${path}" ]] && existing+=("${path}")
  done
  git add -A -- "${existing[@]}"
  if git diff --cached --quiet -- "${existing[@]}"; then
    echo "early import matches the committed code"
  else
    # Commit only the imported paths; unrelated staged changes stay staged.
    git commit -q -m "Import initial ${language} code and schema" -- "${existing[@]}"
    echo "committed early import: $(git rev-parse --short HEAD)"
  fi
fi

progress code-ready "${application_path} and ${schema_path} at $(git rev-parse --short HEAD)"

# The first run needs the initialize boundary; the code usually names one route. Never a stage:
# with none or several candidates main chooses (scripts/benchmark-markers.py set-route).
routes_file=.isuscope/routes.toml
if [[ -n "$(git status --porcelain -- "${routes_file}")" ]]; then
  progress initialize-route "${routes_file} has uncommitted changes; set [initialize] by hand"
elif route_output=$(python3 "${script_dir}/benchmark-markers.py" auto-route 2>&1); then
  if [[ -n "$(git status --porcelain -- "${routes_file}")" ]]; then
    git add -- "${routes_file}"
    git commit -q -m "Set the initialize route found in the imported code" -- "${routes_file}"
  fi
  progress initialize-route "$(printf '%s' "${route_output}" | head -n 1)"
else
  progress initialize-route "needs_choice: $(printf '%s' "${route_output}" | tr '\n' ' ')"
fi

source_node=${CODE_SOURCE_NODE:-$(jq -r '.all.children.application.hosts | keys[0] // empty' "${inventory}")}
echo "== parallel build lane: inspect source node and start with reviewed or proposed settings"
stage build-dispatch python3 "${script_dir}/initial-build.py" start --node "${source_node}" --application "${application_path}"
echo "== AI lanes: claude -p in their own worktrees (config/lanes.json; KICKOFF_LANES= disables)"
# A lane that fails to start never stops the setup; progress.log and lane.py status show it.
stage lanes python3 "${script_dir}/lane.py" kickoff || echo "some AI lanes did not start: python3 scripts/lane.py status" >&2
echo ">> Local environment: adapt config/local/compose.example.yaml; run make local-up in parallel (docs/local-development.md). Baseline does not wait for it."

echo "== setup: bootstrap, inspection and configuration draft"
ANSIBLE_FACT_CACHE_DIR=$(mktemp -d "${repo_dir}/.local/ansible-facts.XXXXXX")
export ANSIBLE_FACT_CACHE_DIR
trap 'rm -rf -- "${ANSIBLE_FACT_CACHE_DIR}"' EXIT
stage bootstrap env ${bootstrap_env[@]+"${bootstrap_env[@]}"} "${script_dir}/bootstrap.sh"
stage inspect "${script_dir}/inspect-environment.sh"
# docs/environment.md from the inspection, without AI; never stops the setup.
stage environment-report python3 "${script_dir}/environment-report.py" || true
stage configure-draft "${script_dir}/configure-draft.sh"

echo "== draft review"
review_status=0
stage review-draft python3 "${script_dir}/review-draft.py" || review_status=$?
if [[ "${review_status}" -eq 0 ]]; then
  progress draft-ready "read .local/draft/review.md, then CONFIRM_DRAFT=true make kickoff-apply"
else
  progress draft-failed "fix the FAILs in .local/draft/review.md"
fi

echo
echo "kickoff stopped before applying the draft"
python3 "${script_dir}/initial-build.py" status || review_status=1
if [[ "${review_status}" -ne 0 ]]; then
  echo "kickoff needs attention: check build status, and .local/draft/review.md"
  exit "${review_status}"
fi
echo "read .local/draft/review.md, decide every WARN, then run: CONFIRM_DRAFT=true make kickoff-apply"
