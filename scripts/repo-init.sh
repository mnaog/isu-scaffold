#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi
repository_name=${1:-}

test -n "${repository_name}" || { echo "usage: $0 OWNER/REPOSITORY or REPOSITORY" >&2; exit 2; }
command -v git >/dev/null
command -v gh >/dev/null
gh auth status >/dev/null

cd "${repo_dir}"
if [[ ! -d .git ]]; then
  git init -b main
fi

test -z "$(git ls-files -- .local)" || { echo '.local contains tracked files' >&2; exit 1; }

if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
  git add -A
  git diff --cached --check
  git commit -m "Initialize ISUCON repository"
fi

if git remote get-url origin >/dev/null 2>&1; then
  echo "origin already exists: $(git remote get-url origin)" >&2
  exit 1
fi

gh repo create "${repository_name}" --private --source . --remote origin --push
gh repo view "${repository_name}" --json visibility --jq '.visibility' | grep -qx PRIVATE
echo "private repository initialized: ${repository_name}"
