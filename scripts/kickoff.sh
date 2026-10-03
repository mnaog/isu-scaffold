#!/usr/bin/env bash
set -euo pipefail

# One entry point for the first hour: early code import for the parallel code lane, then
# full setup up to a reviewed configuration draft. It stops before anything is applied;
# `CONFIRM_DRAFT=true make kickoff-apply` continues after the draft review.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${repo_dir}/.local/operation.lock" -- "$0" "$@"
fi

# shellcheck disable=SC1091
source "${script_dir}/application-policy.sh"
language=${APPLICATION_LANGUAGE:?set APPLICATION_LANGUAGE in config/application.env}
application_path=${APPLICATION_PATH:?set APPLICATION_PATH in config/application.env}
schema_path=${CODE_SCHEMA_PATH:-webapp/sql}
inventory=${ANSIBLE_INVENTORY:-${repo_dir}/.local/ansible-inventory.json}
code_paths=(config/application.env "${application_path}" "${schema_path}")

cd "${repo_dir}"
if git ls-files --error-unmatch -- "${application_path}" >/dev/null 2>&1 &&
  [[ -z "$(git status --porcelain --untracked-files=all -- "${code_paths[@]}")" ]]; then
  echo "== code: ${application_path} is already imported and committed; skipping early import"
else
  echo "== code: early import of ${application_path} and ${schema_path}"
  "${script_dir}/discover.sh"
  source_node=${CODE_SOURCE_NODE:-$(jq -r '.all.children.application.hosts | keys[0] // empty' "${inventory}")}
  test -n "${source_node}" || { echo "no application source node" >&2; exit 2; }
  SSH_ONLY_NODE="${source_node}" "${script_dir}/bootstrap-ssh.sh"
  CODE_SOURCE_NODE="${source_node}" "${script_dir}/quick-import-code.sh" "${language}" "${application_path}"
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

echo "== code lane: worktree"
worktree_output=$("${script_dir}/worktree.sh" "${PHASE1_WORKTREE_BRANCH:-optimize/phase1-obvious}" \
  "Phase 1の初期改善: ${language}のコードとschemaを読み、自明な改善を継続し、観測結果を取り込みながら検証済みcommitを渡す" "HEAD" phase1)
printf '%s\n' "${worktree_output}"
worktree_path=$(printf '%s\n' "${worktree_output}" | sed -n 's/^worktree: //p' | tail -1)
test -n "${worktree_path}" || { echo "worktree path missing" >&2; exit 2; }
source_node=${CODE_SOURCE_NODE:-$(jq -r '.all.children.application.hosts | keys[0] // empty' "${inventory}")}
echo "== parallel build lane: inspect source node and start with reviewed settings"
python3 "${script_dir}/phase1-build.py" start --node "${source_node}" --application "${application_path}"
echo "== parallel worker lane: visible interactive iTerm"
worker_status=0
python3 "${script_dir}/worker-iterm.py" "${worktree_path}" --ensure --dispatch || worker_status=$?
if [[ "${worker_status}" -ne 0 ]]; then
  echo "WORKER START FAILED: retry make worker-start WORKTREE=${worktree_path}; setup continues" >&2
fi

echo ">> Phase 1 local environment: adapt config/local/compose.example.yaml; run make local-up in parallel (docs/local-development.md). Baseline does not wait for it."

echo "== setup: bootstrap, inspection and configuration draft"
"${script_dir}/bootstrap.sh"
"${script_dir}/inspect-environment.sh"
"${script_dir}/configure-draft.sh"

echo "== draft review"
review_status=0
python3 "${script_dir}/review-draft.py" || review_status=$?

echo
echo "kickoff stopped before applying the draft"
if [[ "${worker_status}" -eq 0 ]]; then
  python3 "${script_dir}/worker-iterm.py" "${worktree_path}" --check-started || worker_status=$?
fi
python3 "${script_dir}/phase1-build.py" status || review_status=1
if [[ "${worker_status}" -ne 0 ]]; then
  echo "worker startup still requires attention" >&2
  review_status=1
fi
printf '%s\n' "${worktree_output}" | grep -E '^(worktree|引き継ぎ文):' || true
if [[ "${review_status}" -ne 0 ]]; then
  echo "kickoff needs attention: check worker startup, build status, and .local/draft/review.md"
  exit "${review_status}"
fi
echo "read .local/draft/review.md, decide every WARN, then run: CONFIRM_DRAFT=true make kickoff-apply"
