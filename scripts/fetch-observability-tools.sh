#!/usr/bin/env bash
# 準備で計測tool（slpのrelease）をrepoへ取り込み、当日nodeが
# GitHubへ取りに行かずにAnsibleからcopyで入れられるようにする。版はAnsibleのdefaultsが正。
#   ./scripts/fetch-observability-tools.sh           # 取り込み済みの内容をSHA256SUMSで検査する
#   ./scripts/fetch-observability-tools.sh --update  # defaultsの版を取得し直し、SHA256SUMSを書き直す
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
defaults=${repo_dir}/ansible/roles/observability/defaults/main.yml
vendor=${repo_dir}/ansible/roles/observability/files/vendor
arches=(amd64 arm64)

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi
}

release_tools() {
  sed -n 's/^  - {name: \([a-z]*\), version: \(v[0-9.]*\)}$/\1 \2/p' "${defaults}"
}

if [[ -z "$(release_tools)" ]]; then
  echo "cannot read the pinned tool versions from ${defaults}" >&2
  exit 1
fi

expected_files() {
  local name version arch
  while read -r name version; do
    for arch in "${arches[@]}"; do
      printf '%s_%s_linux_%s.tar.gz\n' "${name}" "${version}" "${arch}"
    done
  done < <(release_tools)
}

if [[ "${1:-}" == --update ]]; then
  work=$(mktemp -d)
  trap 'rm -rf -- "${work}"' EXIT
  while read -r name version; do
    for arch in "${arches[@]}"; do
      curl -fsSL -o "${work}/${name}_${version}_linux_${arch}.tar.gz" \
        "https://github.com/tkuchiki/${name}/releases/download/${version}/${name}_linux_${arch}.tar.gz"
      tar -tzf "${work}/${name}_${version}_linux_${arch}.tar.gz" | grep -qx "${name}" || {
        echo "${name}_linux_${arch}.tar.gz has no ${name} binary at its top level" >&2
        exit 1
      }
    done
  done < <(release_tools)
  rm -rf -- "${vendor}"
  mkdir -p "${vendor}"
  cp -R "${work}/." "${vendor}/"
  (cd "${vendor}" && expected_files | xargs -I{} sh -c 'test -f "{}" || { echo "missing {}" >&2; exit 1; }' && \
    expected_files | while read -r file; do sha256 "${file}"; done >SHA256SUMS)
  echo "fetched into ${vendor#"${repo_dir}"/}; review and commit it with SHA256SUMS"
  exit 0
fi

if [[ ! -f "${vendor}/SHA256SUMS" ]]; then
  echo "no bundled tools; run $0 --update while preparing" >&2
  exit 1
fi
missing=0
while read -r file; do
  if ! grep -q "  ${file}\$" "${vendor}/SHA256SUMS"; then
    echo "not bundled for the pinned version: ${file} (run $0 --update)" >&2
    missing=1
  fi
done < <(expected_files)
(( missing == 0 )) || exit 1
(cd "${vendor}" && if command -v sha256sum >/dev/null 2>&1; then sha256sum --quiet -c SHA256SUMS; else shasum -a 256 -s -c SHA256SUMS; fi)
echo "bundled observability tools match SHA256SUMS"
