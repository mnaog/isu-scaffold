#!/usr/bin/env bash
set -euo pipefail

# Creates one lane: a worktree, its branch, and the purpose recorded on the branch itself
# (git config branch.<name>.description), so nothing has to be updated or cleaned up later.
# Usage: worktree.sh BRANCH PURPOSE [BASE]
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
branch=${1:-}
purpose=${2:-}
base=${3:-${WORKTREE_BASE:-main}}

test -n "${branch}" || { echo "BRANCH=<name> is required" >&2; exit 2; }
test -n "${purpose}" || { echo "PURPOSE=\"何をするlaneか\" is required" >&2; exit 2; }
[[ "${branch}" =~ ^[A-Za-z0-9._/-]+$ && "${branch}" != *".."* ]] || {
  echo "BRANCH must be a simple git branch name" >&2
  exit 2
}

cd "${repo_dir}"
git rev-parse --verify HEAD >/dev/null
git rev-parse --verify "${base}^{commit}" >/dev/null 2>&1 || {
  echo "base branch not found: ${base}" >&2
  exit 1
}
repo_name=$(basename -- "${repo_dir}")
worktree=${WORKTREE_PATH:-$(dirname -- "${repo_dir}")/${repo_name}-${branch##*/}}

lanes() {
  # Every other lane, straight from git: purpose, and how far it is from the base branch.
  git worktree list --porcelain | awk '/^worktree /{path=$2} /^branch /{print path"\t"$2}' |
    while IFS=$'\t' read -r path reference; do
      other=${reference#refs/heads/}
      [[ "${path}" == "${repo_dir}" || "${other}" == "${branch}" ]] && continue
      counts=$(git rev-list --left-right --count "${base}...${other}" 2>/dev/null || echo "0	0")
      ahead=$(printf '%s' "${counts}" | cut -f2)
      description=$(git config "branch.${other}.description" 2>/dev/null ||
        git log -1 --format=%s "${other}" 2>/dev/null || true)
      printf '  %-28s %s\n' "${other}" "${description:-(目的未記入)}"
      if [[ "${ahead}" -eq 0 ]]; then
        state="変更なし"
      elif git merge-base --is-ancestor "${other}" "${base}" 2>/dev/null; then
        state="マージ済み"
      else
        state="未マージ"
      fi
      printf '  %-28s %s+%s / %s\n' "" "${base}" "${ahead}" "${state}"
    done
}

write_brief() {
  mkdir -p "${worktree}/.local"
  {
    printf '# lane: %s\n\n- worktree: %s\n- branch: %s\n- base: %s\n- 目的: %s\n\n' \
      "${branch}" "${worktree}" "${branch}" "${base}" "${purpose}"
    printf '## このlaneでやること\n\n目的だけを小さいcommitで進める。各commitに、直した問題（file:line）、変更、期待する観測値を1行で書く。\n\n'
    printf '## 触ってよい範囲\n\n- webapp/のコードとschema、ローカルのテストだけ\n- deploy、ベンチ、remote操作、.local/operation.lockを使う操作はmain側が行う\n- 初回baselineの分析が終わるまで、変更をremoteへ反映しない\n\n'
    printf '## mainへ渡すとき\n\n目的に対する結果を1行で報告し、mainへマージする前にローカルのbuildとテストを通す。\n\n'
    local others
    others=$(lanes)
    if [[ -n "${others}" ]]; then
      printf '## 他のlane\n\n%s\n' "${others}"
    fi
  } >"${worktree}/.local/lane.md"
}

if [[ -e "${worktree}" ]]; then
  existing=$(git -C "${worktree}" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
  test "${existing}" == "${branch}" || {
    echo "worktree path already exists with a different branch: ${worktree} (${existing:-detached})" >&2
    exit 1
  }
  git config "branch.${branch}.description" "${purpose}"
  write_brief
  echo "lane already exists: ${worktree} (${branch})"
else
  git show-ref --verify --quiet "refs/heads/${branch}" && {
    echo "branch already exists: ${branch}" >&2
    exit 1
  }
  git worktree add -b "${branch}" "${worktree}" "${base}" >/dev/null
  git config "branch.${branch}.description" "${purpose}"
  write_brief
fi

printf 'worktree: %s\nbranch:   %s (base %s)\n目的:     %s\n' \
  "${worktree}" "${branch}" "${base}" "${purpose}"
others=$(lanes)
if [[ -n "${others}" ]]; then
  printf '\n他のlane:\n%s\n' "${others}"
fi
printf '引き継ぎ文: %s\n' "${worktree}/.local/lane.md"
