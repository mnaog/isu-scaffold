#!/usr/bin/env bash
set -euo pipefail

# 最後に成功したdeployのcommit（.local/current-deploy-commit）と、今のHEADと作業ツリーを、
# サーバーへ配る対象（sync.jsonのitem、local buildのDockerfile、sync.json自身）に限って比べます。
# isuscopeはrunをHEADのcommitとして記録するので、ここがずれたままベンチを回すと、
# 記録上のcommitとサーバーで動いているcommitが違うrunになります（deployが未commitの変更で拒否された後に
# 回したときなど）。
# docsやisuscope-dataだけのcommitは配る対象に入らないので、止めません。
# 最初のdeployの前は、make first-benchが配布状態のまま動いていると確かめたcommit
# （.local/current-import-commit、完全importと同じ内容）を、deployしたcommitの代わりに使います。
# deployの記録があればそちらを優先し、deploy前まで戻すrollbackで記録が消えれば再びimportを使います。
# scripts/benchmark-prepare.shが毎回のベンチの前に呼びます。単独でも実行できます。

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=${REPO_DIR:-$(cd -- "${script_dir}/.." && pwd)}
sync=${SYNC_MANIFEST:-${repo_dir}/config/sync.json}
record=${repo_dir}/.local/current-deploy-commit
running="deployed"
command -v jq >/dev/null

if [[ "${SKIP_DEPLOYED_COMMIT_CHECK:-}" == 1 ]]; then
  echo "WARN: deployed commit check skipped (SKIP_DEPLOYED_COMMIT_CHECK=1); the run may be recorded under a commit the servers do not run" >&2
  exit 0
fi
if [[ ! -s "${record}" && -s "${repo_dir}/.local/current-import-commit" ]]; then
  record=${repo_dir}/.local/current-import-commit
  running="imported (distributed build, no deploy yet)"
fi
if [[ ! -s "${record}" ]]; then
  echo "no successful deploy is recorded (.local/current-deploy-commit); run make deploy before the benchmark" >&2
  exit 1
fi
deployed=$(tr -d '[:space:]' <"${record}")
git -C "${repo_dir}" cat-file -e "${deployed}^{commit}" 2>/dev/null || {
  echo "the deployed commit ${deployed} is not in this repository" >&2
  exit 1
}

case "${sync}" in
  "${repo_dir}"/*) manifest_relative=${sync#"${repo_dir}"/} ;;
  *) manifest_relative=${sync} ;;
esac
paths=$( {
  printf '%s\n' "${manifest_relative}"
  # The builder script decides the deployed binary too.
  jq -r '.items[]?.local, (if (.local_builds // []) | length > 0 then "scripts/local-build.py" else empty end)' "${sync}"
} | sort -u)

failed=0
changed=$(cd "${repo_dir}" && printf '%s\n' "${paths}" | tr '\n' '\0' |
  xargs -0 git diff --name-only "${deployed}" HEAD --)
if [[ -n "${changed}" ]]; then
  echo "HEAD $(git -C "${repo_dir}" rev-parse --short=12 HEAD) differs from the ${running} $(git -C "${repo_dir}" rev-parse --short=12 "${deployed}") in files the deploy sends:" >&2
  printf '%s\n' "${changed}" | sed 's/^/  /' >&2
  failed=1
fi
uncommitted=$(cd "${repo_dir}" && printf '%s\n' "${paths}" | tr '\n' '\0' |
  xargs -0 git status --porcelain --untracked-files=all --)
if [[ -n "${uncommitted}" ]]; then
  echo "uncommitted changes in files the deploy sends (the run would be recorded as dirty):" >&2
  printf '%s\n' "${uncommitted}" | sed 's/^/  /' >&2
  failed=1
fi
if [[ "${failed}" == 1 ]]; then
  echo "commit and make deploy, or put HEAD back, before the benchmark" >&2
  exit 1
fi
echo "${running} commit $(git -C "${repo_dir}" rev-parse --short=12 "${deployed}") matches HEAD in the deployed files"
