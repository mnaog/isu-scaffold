#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_repo=$(cd -- "${script_dir}/.." && pwd)
fixture_root=$(mktemp -d "${TMPDIR:-/tmp}/isucon-template-test.XXXXXX")
fixture_repo=${fixture_root}/repo
cleanup() { rm -rf -- "${fixture_root}"; }
trap cleanup EXIT

mkdir -p "${fixture_repo}/scripts" "${fixture_repo}/config" "${fixture_repo}/.isuscope" \
  "${fixture_repo}/.local" "${fixture_repo}/isuscope-data"
cp "${source_repo}"/scripts/discover.sh \
  "${source_repo}"/scripts/with-operation-lock.sh \
  "${source_repo}"/scripts/discover-static.sh \
  "${source_repo}"/scripts/discover-aws.sh \
  "${source_repo}"/scripts/apply-node-overrides.sh \
  "${source_repo}"/scripts/render-environment.sh \
  "${source_repo}"/scripts/ssh-node.sh \
  "${source_repo}"/scripts/timeout-command.py \
  "${source_repo}"/scripts/benchmark-http.sh \
  "${source_repo}"/scripts/sync-lib.sh \
  "${source_repo}"/scripts/sync-check.sh \
  "${source_repo}"/scripts/deploy.sh \
  "${source_repo}"/scripts/rollback.sh \
  "${source_repo}"/scripts/status.sh \
  "${source_repo}"/scripts/import.sh \
  "${source_repo}"/scripts/tree-digest.py \
  "${source_repo}"/scripts/configure-draft.py \
  "${source_repo}"/scripts/configure-draft.sh \
  "${source_repo}"/scripts/configure-apply.sh \
  "${source_repo}"/scripts/suggest-routes.py \
  "${source_repo}"/scripts/suggest-routes.sh \
  "${fixture_repo}/scripts/"
cp "${source_repo}/.isuscope/config.template.toml" \
  "${source_repo}/.isuscope/benchmark.sh" \
  "${fixture_repo}/.isuscope/"
cp "${source_repo}/config/environment.example.env" "${fixture_repo}/.local/environment.env"
cp "${source_repo}/config/nodes.example.json" "${fixture_repo}/.local/nodes.json"
cp "${source_repo}/config/sync.example.json" "${fixture_repo}/config/sync.json"
cp "${source_repo}/config/ansible-vars.json" "${fixture_repo}/config/ansible-vars.json"
jq '.[0] as $app1 | . + [$app1 + {name:"app2", host:"192.0.2.11"}]' \
  "${fixture_repo}/.local/nodes.json" >"${fixture_repo}/.local/nodes.json.tmp"
mv "${fixture_repo}/.local/nodes.json.tmp" "${fixture_repo}/.local/nodes.json"

sed -i.bak 's/^DISCOVERY_PROVIDER=.*/DISCOVERY_PROVIDER=static/' "${fixture_repo}/.local/environment.env"
sed -i.bak 's/^ISUSCOPE_SERVICE_UNITS=.*/ISUSCOPE_SERVICE_UNITS='\''nginx.service isu.service'\''/' "${fixture_repo}/.local/environment.env"
rm -f "${fixture_repo}/.local/environment.env.bak"
(cd "${fixture_repo}" && ./scripts/discover.sh)
test ! -e "${fixture_repo}/.local/operation.lock"

# 生きている変更系操作は拒否し、死んだprocessのlockだけ自動回収します。
mkdir "${fixture_repo}/.local/operation.lock"
printf 'pid=%s\nstarted_at=test\noperation=test\n' "$$" \
  >"${fixture_repo}/.local/operation.lock/owner"
set +e
(cd "${fixture_repo}" && ./scripts/discover.sh >/dev/null 2>&1)
locked_exit=$?
set -e
test "${locked_exit}" -eq 75
rm -f "${fixture_repo}/.local/operation.lock/owner"
rmdir "${fixture_repo}/.local/operation.lock"
mkdir "${fixture_repo}/.local/operation.lock"
printf 'pid=99999999\nstarted_at=test\noperation=stale\n' \
  >"${fixture_repo}/.local/operation.lock/owner"
(cd "${fixture_repo}" && ./scripts/discover.sh)
test ! -e "${fixture_repo}/.local/operation.lock"

jq -e '.all.children.application.hosts.app1.ansible_host == "192.0.2.10"' \
  "${fixture_repo}/.local/ansible-inventory.json" >/dev/null
jq -e '.all.children.application.hosts | length == 2' \
  "${fixture_repo}/.local/ansible-inventory.json" >/dev/null
grep -q '^mode = "command"$' "${fixture_repo}/.isuscope/config.toml"
grep -q '^name = "app1"$' "${fixture_repo}/.isuscope/config.toml"
grep -q '^service_units = \["nginx.service", "isu.service"\]$' "${fixture_repo}/.isuscope/config.toml"
if grep -q '^name = "bench"$' "${fixture_repo}/.isuscope/config.toml"; then
  echo "benchmark node must not receive collectors" >&2
  exit 1
fi
(cd "${fixture_repo}" && ./scripts/sync-check.sh)
(cd "${fixture_repo}" && SYNC_MANIFEST="${source_repo}/config/sync.rust.example.json" \
  ./scripts/sync-check.sh)
jq '.build_commands = [{"name":"invalid-build","node_group":"application","item":"missing-item","command":"true"}]' \
  "${fixture_repo}/config/sync.json" >"${fixture_repo}/.local/invalid-sync.json"
if (cd "${fixture_repo}" && SYNC_MANIFEST=.local/invalid-sync.json ./scripts/sync-check.sh >/dev/null 2>&1); then
  echo "sync manifest accepted a build command for an unknown item" >&2
  exit 1
fi

cat >"${fixture_repo}/config/benchmark.env" <<'EOF'
BENCHMARK_TRANSPORT=local
BENCHMARK_NODE=
BENCHMARK_COMMAND='printf "Score: 42 PASS\n"'
BENCHMARK_PROBE_COMMAND='true'
BENCHMARK_SAMPLE_FILE=config/benchmark-sample.log
BENCHMARK_SCORE_REGEX='([Ss]core[^0-9]*[0-9]+)'
BENCHMARK_PASS_REGEX='PASS'
BENCHMARK_FAIL_REGEX='FAIL'
BENCHMARK_TIMEOUT_SECONDS=0
EOF
printf 'Score: 42 PASS\n' >"${fixture_repo}/config/benchmark-sample.log"

(cd "${fixture_repo}" && ./.isuscope/benchmark.sh --check)
(cd "${fixture_repo}" && ./.isuscope/benchmark.sh --probe)
mkdir -p "${fixture_repo}/.local/test-run/tmp"
result=$(cd "${fixture_repo}" && \
  ISUSCOPE_PROJECT_ROOT="${fixture_repo}" \
  ISUSCOPE_RUN_DIR="${fixture_repo}/.local/test-run" \
  ./.isuscope/benchmark.sh | tail -n 1)
jq -e '.type == "isuscope.result" and .score == 42 and .pass == true' <<<"${result}" >/dev/null

sed -i.bak 's|BENCHMARK_COMMAND=.*|BENCHMARK_COMMAND='\''printf "Score: 42 PASS\\n"; exit 7'\''|' \
  "${fixture_repo}/config/benchmark.env"
rm -f "${fixture_repo}/config/benchmark.env.bak"
failed_result=$(cd "${fixture_repo}" && \
  ISUSCOPE_PROJECT_ROOT="${fixture_repo}" \
  ISUSCOPE_RUN_DIR="${fixture_repo}/.local/test-run" \
  ./.isuscope/benchmark.sh | tail -n 1)
jq -e '.score == 42 and .pass == false' <<<"${failed_result}" >/dev/null

# HTTP API型もstart、poll、score/pass正規化までadapter単体で確認します。
mock_bin=${fixture_repo}/.local/mock-bin
mkdir -p "${mock_bin}"
cat >"${mock_bin}/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
url=${!#}
case "${url}" in
  */probe) exit 0 ;;
  */start) printf '{"id":"run-1"}\n' ;;
  */runs/run-1) printf '{"status":"complete","score":77,"pass":true,"message":"ok"}\n' ;;
  *) echo "unexpected mock URL: ${url}" >&2; exit 1 ;;
esac
EOF
chmod +x "${mock_bin}/curl"
cat >"${fixture_repo}/config/benchmark.env" <<'EOF'
BENCHMARK_TRANSPORT=http
BENCHMARK_NODE=
BENCHMARK_COMMAND=''
BENCHMARK_PROBE_COMMAND=''
BENCHMARK_SAMPLE_FILE=config/benchmark-sample.log
BENCHMARK_SCORE_REGEX='("score"[[:space:]]*:[[:space:]]*[0-9]+)'
BENCHMARK_PASS_REGEX='("pass"[[:space:]]*:[[:space:]]*true)'
BENCHMARK_FAIL_REGEX='("pass"[[:space:]]*:[[:space:]]*false)'
BENCHMARK_TIMEOUT_SECONDS=0
BENCHMARK_HTTP_START_METHOD=POST
BENCHMARK_HTTP_START_URL=https://benchmark.invalid/start
BENCHMARK_HTTP_STATUS_URL=https://benchmark.invalid/runs/{id}
BENCHMARK_HTTP_PROBE_METHOD=GET
BENCHMARK_HTTP_PROBE_URL=https://benchmark.invalid/probe
BENCHMARK_HTTP_HEADERS_FILE=
BENCHMARK_HTTP_BODY_FILE=
BENCHMARK_HTTP_ID_JQ='.id'
BENCHMARK_HTTP_DONE_JQ='.status == "complete"'
BENCHMARK_HTTP_SCORE_JQ='.score'
BENCHMARK_HTTP_PASS_JQ='.pass'
BENCHMARK_HTTP_MESSAGE_JQ='.message // ""'
BENCHMARK_HTTP_POLL_INTERVAL_SECONDS=0
BENCHMARK_HTTP_MAX_POLLS=2
BENCHMARK_HTTP_REQUEST_TIMEOUT_SECONDS=10
EOF
printf '{"score":77,"pass":true}\n' >"${fixture_repo}/config/benchmark-sample.log"
(cd "${fixture_repo}" && PATH="${mock_bin}:${PATH}" ./.isuscope/benchmark.sh --check)
(cd "${fixture_repo}" && PATH="${mock_bin}:${PATH}" ./.isuscope/benchmark.sh --probe)
http_result=$(cd "${fixture_repo}" && \
  PATH="${mock_bin}:${PATH}" \
  ISUSCOPE_PROJECT_ROOT="${fixture_repo}" \
  ISUSCOPE_RUN_DIR="${fixture_repo}/.local/test-run" \
  ./.isuscope/benchmark.sh | tail -n 1)
jq -e '.score == 77 and .pass == true' <<<"${http_result}" >/dev/null

cat >"${mock_bin}/isuscope" <<'EOF'
#!/usr/bin/env bash
cat <<'JSON'
{"rows":[
  {"labels":{"route":"/users/12345/profile"}},
  {"labels":{"route":"/users/67890/profile"}},
  {"labels":{"route":"/items/0123456789abcdef01234567"}}
]}
JSON
EOF
chmod +x "${mock_bin}/isuscope"
(cd "${fixture_repo}" && PATH="${mock_bin}:${PATH}" ./scripts/suggest-routes.sh latest)
grep -Fq 'replace = "/users/:id/profile"' "${fixture_repo}/.local/route-suggestions.toml"
grep -Fq 'replace = "/items/:key"' "${fixture_repo}/.local/route-suggestions.toml"

# importは全nodeのdigest一致を確認してからsource nodeを回収します。
fake_remote=${fixture_repo}/.local/fake-remote
for node in app1 app2; do
  mkdir -p "${fake_remote}/${node}/home/isucon/webapp" "${fake_remote}/${node}/etc/nginx"
  printf 'remote application\n' >"${fake_remote}/${node}/home/isucon/webapp/main.txt"
  printf 'remote nginx\n' >"${fake_remote}/${node}/etc/nginx/nginx.conf"
done
cat >"${fixture_repo}/scripts/ssh-node.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
node=$1
command=$2
root=${FAKE_REMOTE_ROOT:?}/${node}
case "${command}" in
  *tree-digest.py*'/home/isucon/webapp'*) exec python3 "${TREE_DIGEST:?}" "${root}/home/isucon/webapp" ;;
  *tree-digest.py*'/etc/nginx/nginx.conf'*) exec python3 "${TREE_DIGEST:?}" "${root}/etc/nginx/nginx.conf" ;;
  *"tar -C '/home/isucon/webapp'"*) exec tar -C "${root}/home/isucon/webapp" -cf - . ;;
  *"tar -C '/etc/nginx'"*) exec tar -C "${root}/etc/nginx" -cf - nginx.conf ;;
  *) echo "unexpected fake import command: ${command}" >&2; exit 1 ;;
esac
EOF
chmod +x "${fixture_repo}/scripts/ssh-node.sh"
(cd "${fixture_repo}" && \
  FAKE_REMOTE_ROOT="${fake_remote}" TREE_DIGEST="${fixture_repo}/scripts/tree-digest.py" \
  ./scripts/import.sh)
grep -q '^remote application$' "${fixture_repo}/webapp/main.txt"
test "$(wc -l <"${fixture_repo}/.local/import-comparison.tsv" | tr -d ' ')" -eq 5
printf 'divergent\n' >>"${fake_remote}/app2/home/isucon/webapp/main.txt"
set +e
(cd "${fixture_repo}" && \
  FAKE_REMOTE_ROOT="${fake_remote}" TREE_DIGEST="${fixture_repo}/scripts/tree-digest.py" \
  ./scripts/import.sh >/dev/null 2>&1)
divergent_exit=$?
set -e
test "${divergent_exit}" -ne 0
if grep -q divergent "${fixture_repo}/webapp/main.txt"; then
  echo "divergent import changed local files before approval" >&2
  exit 1
fi

# SSHがstdinを読む処理を含んでも全node・全itemを処理することをfake remoteで確認します。
printf 'fixture\n' >"${fixture_repo}/webapp/README.txt"
printf 'events {}\n' >"${fixture_repo}/config/nginx/nginx.conf"
cat >"${fixture_repo}/scripts/ssh-node.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\t%s\n' "$1" "$2" >>"${SSH_CALL_LOG:?}"
if [[ -n "${FAIL_SWITCH_NODE:-}" && "$1" == "${FAIL_SWITCH_NODE}" && "$2" == *"sudo test -e"*isuscope-staging* && ! -e "${FAIL_MARKER:?}" ]]; then
  : >"${FAIL_MARKER}"
  exit 1
fi
if [[ -n "${FAIL_BUILD_NODE:-}" && "$1" == "${FAIL_BUILD_NODE}" && "$2" == *"fixture-build-command"* ]]; then
  exit 1
fi
case "$2" in *"tar -C '"*"' -xf -"*) cat >/dev/null ;; esac
EOF
chmod +x "${fixture_repo}/scripts/ssh-node.sh"
jq '.build_commands = [{"name":"fixture-build","node_group":"application","item":"webapp","command":"test -n \"$ISUCON_DEPLOY_RELEASE\" && test -n \"$ISUCON_DEPLOY_REMOTE_PATH\" && test -n \"$ISUCON_DEPLOY_STAGING_PATH\" # fixture-build-command"}]' \
  "${fixture_repo}/config/sync.json" >"${fixture_repo}/config/sync.json.tmp"
mv "${fixture_repo}/config/sync.json.tmp" "${fixture_repo}/config/sync.json"
(cd "${fixture_repo}" && git init -q && git config user.name test && git config user.email test@example.com && git add . && git commit -qm initial)
ssh_call_log=${fixture_repo}/.local/ssh-calls.log
(cd "${fixture_repo}" && SSH_CALL_LOG="${ssh_call_log}" ./scripts/deploy.sh)
test "$(wc -l <"${ssh_call_log}" | tr -d ' ')" -eq 32
build_line=$(grep -n -m1 'fixture-build-command' "${ssh_call_log}" | cut -d: -f1)
switch_line=$(grep -n -m1 "sudo test -e '/home/isucon/webapp.isuscope-staging.*'; if sudo test" \
  "${ssh_call_log}" | cut -d: -f1)
test "${build_line}" -lt "${switch_line}"
grep -q "export ISUCON_DEPLOY_RELEASE=.*export ISUCON_DEPLOY_REMOTE_PATH=.*export ISUCON_DEPLOY_STAGING_PATH=" \
  "${ssh_call_log}"
release=$(cat "${fixture_repo}/.local/current-release")
(cd "${fixture_repo}" && SSH_CALL_LOG="${ssh_call_log}" ./scripts/rollback.sh "${release}")
test "$(wc -l <"${ssh_call_log}" | tr -d ' ')" -eq 40
build_failure_start=$(wc -l <"${ssh_call_log}" | tr -d ' ')
set +e
(cd "${fixture_repo}" && SSH_CALL_LOG="${ssh_call_log}" FAIL_BUILD_NODE=app2 \
  RELEASE=build-failure ./scripts/deploy.sh >/dev/null 2>&1)
build_failure_exit=$?
set -e
test "${build_failure_exit}" -ne 0
grep -q '^failed$' "${fixture_repo}/.local/deploy-transactions/build-failure.state"
tail -n "+$((build_failure_start + 1))" "${ssh_call_log}" >"${fixture_repo}/.local/build-failure-calls.log"
grep -q 'fixture-build-command' "${fixture_repo}/.local/build-failure-calls.log"
if grep -q "sudo test -e '/home/isucon/webapp.isuscope-staging.build-failure'; if sudo test" \
  "${fixture_repo}/.local/build-failure-calls.log"; then
  echo "deploy switched live files after a staging build failure" >&2
  exit 1
fi
set +e
(cd "${fixture_repo}" && SSH_CALL_LOG="${ssh_call_log}" FAIL_SWITCH_NODE=app2 \
  FAIL_MARKER="${fixture_repo}/.local/fail-marker" RELEASE=transaction-failure ./scripts/deploy.sh >/dev/null 2>&1)
transaction_exit=$?
set -e
test "${transaction_exit}" -ne 0
grep -q '^failed$' "${fixture_repo}/.local/deploy-transactions/transaction-failure.state"

mkdir -p "${fixture_repo}/.local/inspection"
cat >"${fixture_repo}/.local/inspection/app1.json" <<'EOF'
{
  "node":"app1", "running_services":["nginx.service","mysql.service"],
  "processes":["1 root nginx","2 mysql mysqld"],
  "application_candidates":["/home/isucon/webapp/go.mod"],
  "application_candidate_ownership":["/home/isucon/webapp/go.mod\tisucon\tisucon"],
  "configuration_paths":["/etc/nginx/nginx.conf","/etc/mysql"],
  "service_fragments":["nginx.service\t/etc/systemd/system/nginx.service"],
  "nginx_access_logs":["/var/log/nginx/custom.log"],
  "mysql_slow_logs":["/var/log/mysql/slow.log"],
  "versions":{"nginx":"nginx/1","mysql":"mysql 8","perf":"perf 6","sar":"sar 12","alp":"alp 1","slp":"slp 1"}
}
EOF
cat >"${fixture_repo}/.local/inspection/app2.json" <<'EOF'
{
  "node":"app2", "running_services":["nginx.service"],
  "processes":["1 root nginx"],
  "application_candidates":["/home/isucon/webapp/go.mod"],
  "application_candidate_ownership":["/home/isucon/webapp/go.mod\tisucon\tisucon"],
  "configuration_paths":["/etc/nginx/nginx.conf"],
  "service_fragments":["nginx.service\t/etc/systemd/system/nginx.service"],
  "nginx_access_logs":["/var/log/nginx/custom.log"],
  "mysql_slow_logs":[],
  "versions":{"nginx":"nginx/1","mysql":"","perf":"perf 6","sar":"sar 12","alp":"alp 1","slp":"slp 1"}
}
EOF
(cd "${fixture_repo}" && ./scripts/configure-draft.sh)
jq -e '.items | any(.node_group == "role_mysql" and .source_node == "app1")' \
  "${fixture_repo}/.local/draft/sync.json" >/dev/null
jq -e '.items | any(.name == "webapp" and .owner == "isucon" and .owner_group == "isucon")' \
  "${fixture_repo}/.local/draft/sync.json" >/dev/null
jq -e '.post_deploy_commands | any(.node_group == "role_nginx" and .command == "sudo nginx -t")' \
  "${fixture_repo}/.local/draft/sync.json" >/dev/null
jq -e '.ISUSCOPE_SERVICE_UNITS == "mysql.service nginx.service"' \
  "${fixture_repo}/.local/draft/isuscope.json" >/dev/null
(cd "${fixture_repo}" && CONFIRM_DRAFT=true ./scripts/configure-apply.sh)
(cd "${fixture_repo}" && ./scripts/discover.sh && ./scripts/sync-check.sh)
jq -e '.all.children.role_mysql.hosts | keys == ["app1"]' \
  "${fixture_repo}/.local/ansible-inventory.json" >/dev/null
grep -q 'log=/var/log/nginx/custom.log' "${fixture_repo}/.isuscope/config.toml"
grep -q '^service_units = \["mysql.service", "nginx.service"\]$' \
  "${fixture_repo}/.isuscope/config.toml"

echo "initial automation fixture passed"
