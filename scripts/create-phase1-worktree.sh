#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
declaration=${repo_dir}/config/application.env
test -f "${declaration}" || { echo "missing ${declaration}" >&2; exit 1; }
# shellcheck disable=SC1090
source "${declaration}"

test -n "${APPLICATION_LANGUAGE:-}" || {
  echo "set APPLICATION_LANGUAGE and APPLICATION_PATH in config/application.env" >&2
  exit 1
}
test -n "${APPLICATION_PATH:-}" || { echo "APPLICATION_PATH is empty" >&2; exit 1; }
case "${APPLICATION_PATH}" in webapp/*) ;; *) echo "invalid APPLICATION_PATH" >&2; exit 1 ;; esac

cd "${repo_dir}"
git rev-parse --verify HEAD >/dev/null
owned_paths=(config/application.env "${APPLICATION_PATH}")
if [[ -d webapp/sql ]]; then
  owned_paths+=(webapp/sql)
fi
git ls-files --error-unmatch -- "${owned_paths[@]}" >/dev/null
pending=$(git status --porcelain --untracked-files=all -- "${owned_paths[@]}")
test -z "${pending}" || {
  echo "commit the imported application and language declaration before creating the worktree" >&2
  printf '%s\n' "${pending}" >&2
  exit 1
}

repo_name=$(basename -- "${repo_dir}")
branch=${PHASE1_WORKTREE_BRANCH:-optimize/phase1-obvious}
worktree=${PHASE1_WORKTREE_PATH:-$(dirname -- "${repo_dir}")/${repo_name}-phase1-obvious}
schema_note=webapp/sql
if [[ -s "${repo_dir}/.local/code-schema-manifest.log" ]]; then
  schema_note+=" (quick import may be partial; see ${repo_dir}/.local/code-schema-manifest.log)"
fi

# The code lane runs in another session; this brief replaces re-explaining the contract.
write_handoff() {
  mkdir -p "${worktree}/.local"
  cat >"${worktree}/.local/phase1-handoff.md" <<BRIEF
# Phase 1 code lane

- worktree: ${worktree}
- branch: ${branch}
- language: ${APPLICATION_LANGUAGE}
- application: ${APPLICATION_PATH}
- schema: ${schema_note}

## Scope

Read the application and schema, then commit obvious fixes in small commits:
missing indexes, N+1 queries, sequential writes that can be batched, duplicate queries,
single-row ID generation, unnecessary locking reads, and extra DB round trips in transactions.

## Rules

- Change only ${APPLICATION_PATH}, webapp/sql, and local tests.
- Do not deploy, benchmark, touch remote nodes, or use .local/operation.lock; main owns those.
- Do not ship anything before main records and analyzes the untouched baseline.
- Validate locally with the formatter, build, and tests before each commit.

## Report to main

For each commit: the problem (file:line), the change, the expected observable effect
(route, SQL shape, or lock), and how to confirm it against the baseline run.
BRIEF
  echo "handoff brief: ${worktree}/.local/phase1-handoff.md"
}
if [[ -e "${worktree}" ]]; then
  existing_branch=$(git -C "${worktree}" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
  if [[ "${existing_branch}" == "${branch}" ]]; then
    echo "phase1 code worktree already exists: ${worktree} (${branch})"
    write_handoff
    exit 0
  fi
  echo "worktree path already exists with a different branch: ${worktree}" >&2
  exit 1
fi
git show-ref --verify --quiet "refs/heads/${branch}" && {
  echo "branch already exists: ${branch}" >&2
  exit 1
}

git worktree add -b "${branch}" "${worktree}" HEAD
printf 'phase1 code worktree: %s\nbranch: %s\nlanguage: %s\napplication: %s\n' \
  "${worktree}" "${branch}" "${APPLICATION_LANGUAGE}" "${APPLICATION_PATH}"
write_handoff
