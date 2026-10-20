#!/usr/bin/env bash
# 準備でisuscope-node（nodeでaccess logの差分を集計するbinary）をbuildし、.local/isuscope-node/へ置く。
# bootstrapのobservability roleが、nodeのarchitectureに合うものを/usr/local/bin/isuscope-nodeへcopyし、
# isuscopeのalp collectorはawkとalpの代わりにそれを使う（2コアで62万行を約20秒から約1.2秒へ）。
# muslの静的binaryなのでnodeのOSに依存せず、問題の公開前に作れる。
#   ./scripts/build-isuscope-node.sh           # installしたisuscopeのsourceからamd64とarm64をbuildする
#   ./scripts/build-isuscope-node.sh --check   # 置いたbinaryが、installしたisuscopeと同じbuildか確かめる
# sourceは`cargo install --path`が$CARGO_HOME/.crates2.jsonに残したisuscopeのinstall元。別の場所なら
# ISUSCOPE_SOURCE=<isuscopeのrepo>で渡す。
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
out=${repo_dir}/.local/isuscope-node

installed=$(isuscope __access-summary --build-id 2>/dev/null) || {
  echo "the installed isuscope has no node aggregation (isuscope __access-summary); install a newer isuscope first" >&2
  exit 1
}

if [[ "${1:-}" == --check ]]; then
  test -f "${out}/BUILD_ID" || { echo "isuscope-node is not built; run $0 while preparing" >&2; exit 1; }
  built=$(cat "${out}/BUILD_ID")
  if [[ "${built}" != "${installed}" ]]; then
    echo "isuscope-node in .local/isuscope-node is build ${built}, the installed isuscope expects ${installed}; run $0 again" >&2
    exit 1
  fi
  (cd "${out}" && if command -v sha256sum >/dev/null 2>&1; then sha256sum --quiet -c SHA256SUMS; else shasum -a 256 -s -c SHA256SUMS; fi)
  echo "isuscope-node ${built} matches the installed isuscope"
  exit 0
fi

source_dir=${ISUSCOPE_SOURCE:-}
if [[ -z "${source_dir}" ]]; then
  source_dir=$(python3 - "${CARGO_HOME:-${HOME}/.cargo}/.crates2.json" <<'PY'
import json, re, sys, urllib.parse
try:
    installs = json.load(open(sys.argv[1]))["installs"]
except (OSError, ValueError, KeyError):
    sys.exit(0)
for key in installs:
    match = re.fullmatch(r"isuscope \S+ \(path\+file://(.+)\)", key)
    if match:
        print(urllib.parse.unquote(match.group(1)))
PY
)
fi
if [[ -z "${source_dir}" || ! -x "${source_dir}/scripts/build-node.sh" ]]; then
  echo "cannot find isuscope's scripts/build-node.sh${source_dir:+ under ${source_dir}}; set ISUSCOPE_SOURCE to the isuscope repository" >&2
  exit 1
fi

"${source_dir}/scripts/build-node.sh" "${out}"
built=$(cat "${out}/BUILD_ID")
if [[ "${built}" != "${installed}" ]]; then
  echo "built isuscope-node ${built} from ${source_dir}, but the installed isuscope expects ${installed}; reinstall isuscope with cargo install --path ${source_dir} --locked and run $0 again" >&2
  exit 1
fi
echo "isuscope-node ${built} is in .local/isuscope-node; bootstrap installs it on every application node"
