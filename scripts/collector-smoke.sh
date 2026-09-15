#!/usr/bin/env bash
set -euo pipefail

# Read-only pre-benchmark smoke test for collector inputs. It never starts a benchmark:
# required log sources and a short /proc sample must work, optional tools only warn.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
config=${ISUSCOPE_CONFIG:-${repo_dir}/.isuscope/config.toml}
benchmark_env=${repo_dir}/config/benchmark.env
source "${script_dir}/parallel-lib.sh"

command -v python3 >/dev/null
test -f "${config}" || { echo "missing ${config}; run make discover first" >&2; exit 1; }

# name<TAB>space-separated roles<TAB>nginx log<TAB>slow log
node_rows=$(python3 - "${config}" <<'PY'
import re, sys, tomllib
config = tomllib.load(open(sys.argv[1], "rb"))
def log_path(name):
    for collector in config.get("collectors", []):
        if collector.get("name") == name:
            match = re.search(r"^log=(\S+)$", collector["command"][-1], re.M)
            if match:
                return match.group(1)
    return "-"
nginx, slow = log_path("nginx-log-mark"), log_path("mysql-log-mark")
for node in config.get("nodes", []):
    print("\t".join([node["name"], " ".join(node.get("roles", [])) or "-", nginx, slow]))
PY
)
test -n "${node_rows}" || { echo "no isuscope nodes configured" >&2; exit 1; }

workspace=$(mktemp -d "${TMPDIR:-/tmp}/collector-smoke.XXXXXX")
trap 'rm -rf -- "${workspace}"' EXIT

smoke_node() {
  local node=$1 roles=" $2 " nginx_log=$3 slow_log=$4 remote
  remote="set -u; failed=0
command -v sha256sum >/dev/null 2>&1 || { echo 'FAIL sha256sum missing'; failed=1; }
a=\$(head -n 1 /proc/stat); sleep 1; b=\$(head -n 1 /proc/stat)
test \"\$a\" != \"\$b\" && echo 'OK /proc/stat sampled' || { echo 'FAIL /proc/stat did not advance'; failed=1; }
command -v sar >/dev/null 2>&1 || echo 'WARN sar missing (sysstat unavailable)'"
  if [[ "${roles}" == *" active "* ]]; then
    remote+="
test -r '${nginx_log}' && echo 'OK nginx log readable: ${nginx_log}' || { echo 'FAIL nginx log unreadable: ${nginx_log}'; failed=1; }
command -v alp >/dev/null 2>&1 || echo 'WARN alp missing'"
  fi
  if [[ "${roles}" == *" db "* ]]; then
    remote+="
sudo -n test -r '${slow_log}' && echo 'OK slow log readable via sudo: ${slow_log}' || { echo 'FAIL slow log unreadable via sudo -n: ${slow_log}'; failed=1; }
command -v slp >/dev/null 2>&1 || echo 'WARN slp missing'"
  fi
  remote+="
exit \$failed"
  "${script_dir}/ssh-node.sh" "${node}" "${remote}" >"${workspace}/${node}.log" 2>&1
}

parallel_init "${COLLECTOR_SMOKE_MAX_PARALLEL_NODES:-5}"
while IFS=$'\t' read -r node roles nginx_log slow_log; do
  parallel_start smoke_node "${node}" "${roles}" "${nginx_log}" "${slow_log}"
done <<<"${node_rows}"
nodes_ok=true
parallel_wait || nodes_ok=false
while IFS=$'\t' read -r node _; do
  sed "s/^/${node}: /" "${workspace}/${node}.log"
done <<<"${node_rows}"

parser_ok=true
sample=
if [[ -f "${benchmark_env}" ]]; then
  sample=$(sed -n 's/^BENCHMARK_SAMPLE_FILE=//p' "${benchmark_env}" | tail -n 1 | tr -d "\"'")
fi
if [[ -n "${sample}" && -f "${repo_dir}/${sample}" && -x "${repo_dir}/.isuscope/parse-benchmark.sh" ]]; then
  command -v zstd >/dev/null
  zstd -q -o "${workspace}/sample.zst" "${repo_dir}/${sample}"
  if (cd "${repo_dir}" && .isuscope/parse-benchmark.sh "${workspace}/sample.zst") >"${workspace}/parsed.jsonl"; then
    if python3 -c '
import json, sys
count = 0
for line in open(sys.argv[1]):
    if line.strip():
        assert json.loads(line).get("type"), line
        count += 1
print(f"parser: {count} records from sample")
' "${workspace}/parsed.jsonl"; then
      [[ -s "${workspace}/parsed.jsonl" ]] || echo "WARN parser produced no records from ${sample}"
    else
      echo "FAIL parser emitted invalid JSONL for ${sample}" >&2
      parser_ok=false
    fi
  else
    echo "FAIL parser exited non-zero for ${sample}" >&2
    parser_ok=false
  fi
else
  echo "WARN no benchmark sample; parser was not smoke-tested"
fi

[[ "${nodes_ok}" == true && "${parser_ok}" == true ]] || { echo "collector smoke failed" >&2; exit 1; }
echo "collector smoke passed"
