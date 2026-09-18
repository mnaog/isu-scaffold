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
  "${source_repo}"/scripts/parallel-lib.sh \
  "${source_repo}"/scripts/schema-archive.py \
  "${source_repo}"/scripts/tree-digest.py \
  "${source_repo}"/scripts/configure-draft.py \
  "${source_repo}"/scripts/review-draft.py \
  "${source_repo}"/scripts/configure-draft.sh \
  "${source_repo}"/scripts/configure-apply.sh \
  "${source_repo}"/scripts/quick-import-code.sh \
  "${source_repo}"/scripts/worktree.sh \
  "${fixture_repo}/scripts/"
# collectorの正本はisuscopeが配るので、fixtureにはbenchmark adapterだけを置く。
cp "${source_repo}/.isuscope/benchmark.sh" "${fixture_repo}/.isuscope/"
cp "${source_repo}/config/environment.example.env" "${fixture_repo}/.local/environment.env"
cp "${source_repo}/config/nodes.example.json" "${fixture_repo}/.local/nodes.json"
cp "${source_repo}/tests/fixtures/sync.json" "${fixture_repo}/config/sync.json"
cp "${source_repo}/config/sync.rust.example.json" "${fixture_repo}/config/sync.rust.example.json"
cp "${source_repo}/config/ansible-vars.json" "${fixture_repo}/config/ansible-vars.json"
cp "${source_repo}/config/application.env" "${fixture_repo}/config/application.env"
jq '.[0] as $app1 | . + [$app1 + {name:"app2", host:"192.0.2.11"}]' \
  "${fixture_repo}/.local/nodes.json" >"${fixture_repo}/.local/nodes.json.tmp"
mv "${fixture_repo}/.local/nodes.json.tmp" "${fixture_repo}/.local/nodes.json"

sed -i.bak 's/^DISCOVERY_PROVIDER=.*/DISCOVERY_PROVIDER=static/' "${fixture_repo}/.local/environment.env"
sed -i.bak 's/^ISUSCOPE_SERVICE_UNITS=.*/ISUSCOPE_SERVICE_UNITS='\''nginx.service isu.service'\''/' "${fixture_repo}/.local/environment.env"
rm -f "${fixture_repo}/.local/environment.env.bak"
(cd "${fixture_repo}" && ./scripts/discover.sh)
# lockはflockなので、解放後もlock fileは残り、誰が握っていたかの説明（owner）だけが消えます。
test ! -e "${fixture_repo}/.local/operation.lock/owner"

# isuscope lockは生きている変更系操作を拒否します。
holding=${fixture_root}/holding
(cd "${fixture_repo}" && isuscope lock --path .local/operation.lock -- \
  sh -c 'touch "$1"; while test -e "$1"; do sleep 0.05; done' sh "${holding}") &
holder=$!
while test ! -e "${holding}"; do sleep 0.05; done
set +e
(cd "${fixture_repo}" && ./scripts/discover.sh >/dev/null 2>&1)
locked_exit=$?
set -e
test "${locked_exit}" -eq 75
rm -f "${holding}"
wait "${holder}"

# 落ちたprocessが残したownerは、次の操作を止めません（lockはkernelが外しています）。
printf 'pid=99999999\nstarted_at=test\noperation=stale\n' \
  >"${fixture_repo}/.local/operation.lock/owner"
(cd "${fixture_repo}" && ./scripts/discover.sh)
test ! -e "${fixture_repo}/.local/operation.lock/owner"

jq -e '.all.children.application.hosts.app1.ansible_host == "192.0.2.10"' \
  "${fixture_repo}/.local/ansible-inventory.json" >/dev/null
jq -e '.all.children.application.hosts | length == 2' \
  "${fixture_repo}/.local/ansible-inventory.json" >/dev/null
grep -q '^mode = "command"$' "${fixture_repo}/.isuscope/config.toml"
grep -q '^known_hosts_file = ".local/known-hosts"$' "${fixture_repo}/.isuscope/config.toml"
grep -q '^path = ".local/operation.lock"$' "${fixture_repo}/.isuscope/config.toml"
python3 - "${fixture_repo}/.isuscope/config.toml" <<'PY'
import sys, tomllib
config = tomllib.load(open(sys.argv[1], "rb"))
roles = {collector["name"]: collector.get("roles", []) for collector in config["collectors"]}
assert roles["perf-series"] == ["app"], roles["perf-series"]
assert roles["nginx-log-mark"] == ["nginx", "edge"], roles["nginx-log-mark"]
assert roles["mysql-log-mark"] == ["db", "mysql"], roles["mysql-log-mark"]
assert roles["host-sampler"] == [], roles["host-sampler"]
PY
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


# draft reviewはnodeごとの事実をSSHで集め、危険な配置先・実在しないpath・停止中serviceをFAILにします。
review_dir=${fixture_repo}/.local/review
mkdir -p "${review_dir}/draft" "${review_dir}/facts"
cat >"${review_dir}/ssh-node" <<'EOF'
#!/usr/bin/env bash
case "$2" in
  *"nginx -T"*) cat "${REVIEW_FACTS_DIR:?}/$1.nginx" 2>/dev/null || true ;;
  *) cat "${REVIEW_FACTS_DIR:?}/$1" ;;
esac
EOF
chmod +x "${review_dir}/ssh-node"
printf '[{"name":"app1","roles":["app","nginx","db"]},{"name":"app2","roles":["app","nginx","db"]}]\n' \
  >"${review_dir}/draft/node-overrides.json"
printf '{}\n' >"${review_dir}/draft/ansible-vars.json"
printf '{"ISUSCOPE_NGINX_ACCESS_LOG":"/var/log/nginx/access.log","ISUSCOPE_MYSQL_SLOW_LOG":"/var/log/mysql/mysql-slow.log"}\n' \
  >"${review_dir}/draft/isuscope.json"
write_facts() {
  local node=$1 webapp_kind=$2 webapp_kb=$3 service_state=$4 dropin=${5:-yes}
  printf 'path\t/home/isucon/webapp/rust\t%s\t%s\nuser\tisucon\tyes\ngroup\tisucon\tyes\nservice\tisu.service\t%s\nlog\t/var/log/nginx/access.log\tyes\nlog\t/var/log/mysql/mysql-slow.log\tno\ndropin\t/etc/nginx/conf.d/00-isuscope-log.conf\t%s\n' \
    "${webapp_kind}" "${webapp_kb}" "${service_state}" "${dropin}" >"${review_dir}/facts/${node}"
}
write_sync() {
  jq -n --arg remote "$1" '{source_node:"app1", items:[{name:"webapp", type:"directory", node_group:"application", local:"webapp/rust", remote:$remote, owner:"isucon", owner_group:"isucon"}], post_deploy_commands:["sudo systemctl is-active --quiet isu.service"], status_commands:[]}' \
    >"${review_dir}/draft/sync.json"
}
run_review() {
  REVIEW_FACTS_DIR="${review_dir}/facts" python3 "${fixture_repo}/scripts/review-draft.py" \
    "${fixture_repo}/.local/ansible-inventory.json" "${review_dir}/draft" "${review_dir}/ssh-node"
}

write_sync /home/isucon/webapp/rust
write_facts app1 directory 2048 active
# serverブロックのaccess_log（offを含む）はhttpレベルの計測logを上書きするのでWARNにします。
cat >"${review_dir}/facts/app1.nginx" <<'EOF'
# configuration file /etc/nginx/nginx.conf:
http {
    access_log /var/log/nginx/access.log;
    include /etc/nginx/sites-enabled/*;
}
# configuration file /etc/nginx/sites-enabled/app.conf:
server {
    listen 443 ssl; # comment ; with separators {
    access_log off;
    location / { proxy_pass http://127.0.0.1:8080; }
}
EOF
write_facts app2 directory 999999 active
run_review >/dev/null
grep -q '^FAIL 0 / WARN 4 ' "${review_dir}/draft/review.md"
grep -q 'WARN app1: server block in /etc/nginx/sites-enabled/app.conf sets access_log off' "${review_dir}/draft/review.md"
if grep -q 'nginx.conf sets access_log' "${review_dir}/draft/review.md"; then
  echo "http-level access_log was reported as a server override" >&2
  exit 1
fi
grep -q 'WARN app2: webapp is .* MiB' "${review_dir}/draft/review.md"
grep -q 'WARN app1: log not readable: /var/log/mysql/mysql-slow.log' "${review_dir}/draft/review.md"

write_sync /home/isucon
write_facts app2 missing 0 inactive no
set +e
run_review >/dev/null
review_exit=$?
set -e
test "${review_exit}" -eq 1
grep -q 'FAIL webapp would replace a broad system path: /home/isucon' "${review_dir}/draft/review.md"
grep -q 'FAIL app2: webapp remote path does not exist' "${review_dir}/draft/review.md"
grep -q 'FAIL app2: service isu.service is inactive' "${review_dir}/draft/review.md"
grep -q 'FAIL app2: measurement drop-in is missing: /etc/nginx/conf.d/00-isuscope-log.conf' "${review_dir}/draft/review.md"

# importは全nodeのdigest一致を確認してからsource nodeを回収します。
fake_remote=${fixture_repo}/.local/fake-remote
for node in app1 app2; do
  mkdir -p "${fake_remote}/${node}/home/isucon/webapp/rust/target" \
    "${fake_remote}/${node}/home/isucon/webapp/sql" "${fake_remote}/${node}/etc/nginx"
  printf 'remote application\n' >"${fake_remote}/${node}/home/isucon/webapp/main.txt"
  printf 'fn main() {}\n' >"${fake_remote}/${node}/home/isucon/webapp/rust/main.rs"
  printf 'generated artifact\n' >"${fake_remote}/${node}/home/isucon/webapp/rust/target/debug.bin"
  printf 'CREATE TABLE fixture (id BIGINT);\n' >"${fake_remote}/${node}/home/isucon/webapp/sql/schema.sql"
  printf '1\tinitial\n' >"${fake_remote}/${node}/home/isucon/webapp/sql/initial-data.tsv"
  printf 'remote nginx\n' >"${fake_remote}/${node}/etc/nginx/nginx.conf"
done
cat >"${fixture_repo}/scripts/ssh-node.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
node=$1
command=$2
root=${FAKE_REMOTE_ROOT:?}/${node}
case "${command}" in
  *ISUCON_DIGEST_BATCH*)
    exec bash -c "$(sed -e "s|sudo python3 /usr/local/lib/isuscope/tree-digest.py '|python3 '${TREE_DIGEST:?}' '${root}|g" <<<"${command}")" ;;
  "sudo test -d '/home/isucon/webapp/rust'") test -d "${root}/home/isucon/webapp/rust" ;;
  "sudo test -d '/home/isucon/webapp/sql'") test -d "${root}/home/isucon/webapp/sql" ;;
  *tree-digest.py*'/home/isucon/webapp'*) exec python3 "${TREE_DIGEST:?}" "${root}/home/isucon/webapp" ;;
  *tree-digest.py*'/etc/nginx/nginx.conf'*) exec python3 "${TREE_DIGEST:?}" "${root}/etc/nginx/nginx.conf" ;;
  *"tar -C '/home/isucon/webapp/rust'"*) exec tar -C "${root}/home/isucon/webapp/rust" --exclude='./target' -cf - . ;;
  *"sudo -n python3 -c"*) exec bash -c "$(sed -e 's|^sudo -n ||' -e "s| /home/isucon/webapp/sql | ${root}/home/isucon/webapp/sql |" <<<"${command}")" ;;
  *"tar -C '/home/isucon/webapp'"*) exec tar -C "${root}/home/isucon/webapp" -cf - . ;;
  *"tar -C '/etc/nginx'"*) exec tar -C "${root}/etc/nginx" -cf - nginx.conf ;;
  *) echo "unexpected fake import command: ${command}" >&2; exit 1 ;;
esac
EOF
chmod +x "${fixture_repo}/scripts/ssh-node.sh"
(cd "${fixture_repo}" && git init -q && git config user.name test && git config user.email test@example.com && \
  git add . && git commit -qm fixture-base)
(cd "${fixture_repo}" && FAKE_REMOTE_ROOT="${fake_remote}" ./scripts/quick-import-code.sh rust)
grep -q '^fn main()' "${fixture_repo}/webapp/rust/main.rs"
grep -q '^CREATE TABLE fixture' "${fixture_repo}/webapp/sql/schema.sql"
# 初期データはコードレーンの開始を待たせないよう後回しにします。
test ! -e "${fixture_repo}/webapp/sql/initial-data.tsv"
grep -q '^DEFERRED data-or-link initial-data.tsv$' "${fixture_repo}/.local/code-schema-manifest.log"
test ! -e "${fixture_repo}/webapp/rust/target"
grep -q '^APPLICATION_LANGUAGE=rust$' "${fixture_repo}/config/application.env"
# laneは目的の記載が必須です。
set +e
(cd "${fixture_repo}" && WORKTREE_PATH="${fixture_root}/no-purpose" \
  ./scripts/worktree.sh optimize/no-purpose "" >/dev/null 2>&1)
no_purpose_exit=$?
set -e
test "${no_purpose_exit}" -eq 2
test ! -e "${fixture_root}/no-purpose"
(cd "${fixture_repo}" && \
  FAKE_REMOTE_ROOT="${fake_remote}" TREE_DIGEST="${fixture_repo}/scripts/tree-digest.py" \
  ./scripts/import.sh)
grep -q '^remote application$' "${fixture_repo}/webapp/main.txt"
test "$(wc -l <"${fixture_repo}/.local/import-comparison.tsv" | tr -d ' ')" -eq 5
# 内容が一致するlocal itemは再転送しません。
reimport_output=$(cd "${fixture_repo}" && \
  FAKE_REMOTE_ROOT="${fake_remote}" TREE_DIGEST="${fixture_repo}/scripts/tree-digest.py" \
  ./scripts/import.sh 2>/dev/null)
grep -q 'reusing unchanged local item' <<<"${reimport_output}"
if grep -q '^importing ' <<<"${reimport_output}"; then
  echo "unchanged import transferred items again" >&2
  exit 1
fi
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
mkdir -p "${fixture_repo}/webapp/rust"
printf 'fn main() {}\n' >"${fixture_repo}/webapp/rust/main.rs"
printf 'events {}\n' >"${fixture_repo}/config/nginx/nginx.conf"
cat >"${fixture_repo}/scripts/ssh-node.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\t%s\n' "$1" "$2" >>"${SSH_CALL_LOG:?}"
if [[ -n "${FAIL_SWITCH_NODE:-}" && "$1" == "${FAIL_SWITCH_NODE}" && \
  "$2" == *"sudo test -e '/home/isucon/webapp.isuscope-staging."*"'; if sudo test"* && \
  ! -e "${FAIL_MARKER:?}" ]]; then
  : >"${FAIL_MARKER}"
  exit 1
fi
if [[ -n "${FAIL_BUILD_NODE:-}" && "$1" == "${FAIL_BUILD_NODE}" && "$2" == *"fixture-build-command"* ]]; then
  exit 1
fi
if [[ -n "${FAIL_PREFLIGHT_NODE:-}" && "$1" == "${FAIL_PREFLIGHT_NODE}" && "$2" == *"sudo -n true"* ]]; then
  echo "no space left on device" >&2
  exit 1
fi
if [[ -n "${FAIL_RESTORE_NODE:-}" && "$1" == "${FAIL_RESTORE_NODE}" && "$2" == *"failed=0"* ]]; then
  echo "restore: mv failed" >&2
  exit 1
fi
case "$2" in
  *"tar -C '"*"' -xf -"*)
    if [[ -n "${ARCHIVE_LIST_LOG:-}" ]]; then
      tar -tf - >>"${ARCHIVE_LIST_LOG}"
    else
      cat >/dev/null
    fi
    ;;
esac
EOF
chmod +x "${fixture_repo}/scripts/ssh-node.sh"
jq '.build_commands = [{"name":"fixture-build","node_group":"application","item":"webapp","command":"test -n \"$ISUCON_DEPLOY_RELEASE\" && test -n \"$ISUCON_DEPLOY_REMOTE_PATH\" && test -n \"$ISUCON_DEPLOY_STAGING_PATH\" # fixture-build-command"}] |
  .rollback_commands = [{"node_group":"application","command":"echo fixture-runtime-rollback"}]' \
  "${fixture_repo}/config/sync.json" >"${fixture_repo}/config/sync.json.tmp"
mv "${fixture_repo}/config/sync.json.tmp" "${fixture_repo}/config/sync.json"
(cd "${fixture_repo}" && git add . && git commit -qm initial)
ssh_call_log=${fixture_repo}/.local/ssh-calls.log
# deploy archiveはGitのHEADから作るため、ignored artifactを配布しません。
archive_list_log=${fixture_repo}/.local/archive-list.log
printf '/webapp/ignored-artifact\n' >>"${fixture_repo}/.git/info/exclude"
printf 'must not deploy\n' >"${fixture_repo}/webapp/ignored-artifact"
(cd "${fixture_repo}" && SSH_CALL_LOG="${ssh_call_log}" \
  ARCHIVE_LIST_LOG="${archive_list_log}" DEPLOY_MAX_PARALLEL_NODES=2 ./scripts/deploy.sh)
grep -q '^webapp/README.txt$' "${archive_list_log}"
if grep -q 'ignored-artifact' "${archive_list_log}"; then
  echo "deploy archive included an ignored artifact" >&2
  exit 1
fi
build_input_id() {
  grep 'fixture-build-command' "$1" | head -n 1 | \
    sed -E "s/.*ISUCON_DEPLOY_BUILD_INPUT_ID='([^']+)'.*/\\1/"
}
grep -q "ISUCON_DEPLOY_BUILD_INPUTS_UNCHANGED='false'" "${ssh_call_log}"
initial_build_input_id=$(build_input_id "${ssh_call_log}")
test -n "${initial_build_input_id}"
build_line=$(grep -n -m1 'fixture-build-command' "${ssh_call_log}" | cut -d: -f1)
switch_line=$(grep -n -m1 "sudo test -e '/home/isucon/webapp.isuscope-staging.*'; if sudo test" \
  "${ssh_call_log}" | cut -d: -f1)
test "${build_line}" -lt "${switch_line}"
grep -q "export ISUCON_DEPLOY_RELEASE=.*export ISUCON_DEPLOY_REMOTE_PATH=.*export ISUCON_DEPLOY_STAGING_PATH=" \
  "${ssh_call_log}"
grep -q "find '/home/isucon'.*isuscope-backup" "${ssh_call_log}"
first_release=$(cat "${fixture_repo}/.local/current-release")
test "$(cat "${fixture_repo}/.local/current-commit")" = \
  "$(git -C "${fixture_repo}" rev-parse HEAD)"

# 同じcommitを続けてdeployしてもtransaction IDが衝突しません。
(cd "${fixture_repo}" && SSH_CALL_LOG="${ssh_call_log}" ./scripts/deploy.sh)
second_release=$(cat "${fixture_repo}/.local/current-release")
test "${first_release}" != "${second_release}"
rollback_start=$(wc -l <"${ssh_call_log}" | tr -d ' ')
(cd "${fixture_repo}" && SSH_CALL_LOG="${ssh_call_log}" ./scripts/rollback.sh "${second_release}")
tail -n "+$((rollback_start + 1))" "${ssh_call_log}" >"${fixture_repo}/.local/explicit-rollback-calls.log"
grep -q 'fixture-runtime-rollback' "${fixture_repo}/.local/explicit-rollback-calls.log"
test "$(cat "${fixture_repo}/.local/current-release")" = "${first_release}"
test "$(cat "${fixture_repo}/.local/current-commit")" = \
  "$(git -C "${fixture_repo}" rev-parse HEAD)"

# build対象とbuild commandが前回成功時と同じdeployだけをcache再利用候補にします。
unchanged_build_start=$(wc -l <"${ssh_call_log}" | tr -d ' ')
printf 'events { worker_connections 2; }\n' >"${fixture_repo}/config/nginx/nginx.conf"
(cd "${fixture_repo}" && git add config/nginx/nginx.conf && git commit -qm config-only)
(cd "${fixture_repo}" && SSH_CALL_LOG="${ssh_call_log}" ./scripts/deploy.sh)
tail -n "+$((unchanged_build_start + 1))" "${ssh_call_log}" >"${fixture_repo}/.local/unchanged-build-calls.log"
grep -q "ISUCON_DEPLOY_BUILD_INPUTS_UNCHANGED='true'" "${fixture_repo}/.local/unchanged-build-calls.log"
test "$(build_input_id "${fixture_repo}/.local/unchanged-build-calls.log")" = "${initial_build_input_id}"

changed_build_start=$(wc -l <"${ssh_call_log}" | tr -d ' ')
printf 'changed build input\n' >>"${fixture_repo}/webapp/README.txt"
(cd "${fixture_repo}" && git add webapp/README.txt && git commit -qm app-change)
(cd "${fixture_repo}" && SSH_CALL_LOG="${ssh_call_log}" ./scripts/deploy.sh)
tail -n "+$((changed_build_start + 1))" "${ssh_call_log}" >"${fixture_repo}/.local/changed-build-calls.log"
grep -q "ISUCON_DEPLOY_BUILD_INPUTS_UNCHANGED='false'" "${fixture_repo}/.local/changed-build-calls.log"
test "$(build_input_id "${fixture_repo}/.local/changed-build-calls.log")" != "${initial_build_input_id}"

overlap_manifest=${fixture_repo}/.local/sync-overlap.json
jq '.items += [(.items[0] | .name = "overlap" | .local = (.local + "/nested") | .remote = "/tmp/isuscope-overlap")]' \
  "${fixture_repo}/config/sync.json" >"${overlap_manifest}"
set +e
overlap_output=$(cd "${fixture_repo}" && SYNC_MANIFEST="${overlap_manifest}" ./scripts/sync-check.sh 2>&1)
overlap_exit=$?
set -e
test "${overlap_exit}" -ne 0
grep -q 'must not overlap' <<<"${overlap_output}"

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
if grep -q 'fixture-runtime-rollback' "${fixture_repo}/.local/build-failure-calls.log"; then
  echo "deploy ran runtime rollback before switching live files" >&2
  exit 1
fi
# preflightはremoteを変えないので、そこで失敗しても復旧処理を走らせません。
preflight_failure_start=$(wc -l <"${ssh_call_log}" | tr -d ' ')
set +e
preflight_failure_output=$(cd "${fixture_repo}" && SSH_CALL_LOG="${ssh_call_log}" \
  FAIL_PREFLIGHT_NODE=app2 RELEASE=preflight-failure ./scripts/deploy.sh 2>&1)
preflight_failure_exit=$?
set -e
test "${preflight_failure_exit}" -ne 0
tail -n "+$((preflight_failure_start + 1))" "${ssh_call_log}" \
  >"${fixture_repo}/.local/preflight-failure-calls.log"
if grep -q 'failed=0' "${fixture_repo}/.local/preflight-failure-calls.log"; then
  echo "deploy restored remote paths after a preflight failure" >&2
  exit 1
fi
grep -q 'failed before any remote change' <<<"${preflight_failure_output}"

# 同じrelease名を再利用すると、前回の退避を今回のものと誤認します。
set +e
duplicate_output=$(cd "${fixture_repo}" && SSH_CALL_LOG="${ssh_call_log}" \
  RELEASE=preflight-failure ./scripts/deploy.sh 2>&1)
duplicate_exit=$?
set -e
test "${duplicate_exit}" -ne 0
grep -q 'was already used' <<<"${duplicate_output}"

# 復旧が失敗したら、deployはそれを出力して失敗として扱います。
restore_failure_start=$(wc -l <"${ssh_call_log}" | tr -d ' ')
set +e
restore_failure_output=$(cd "${fixture_repo}" && SSH_CALL_LOG="${ssh_call_log}" \
  FAIL_SWITCH_NODE=app2 FAIL_MARKER="${fixture_repo}/.local/restore-fail-marker" \
  FAIL_RESTORE_NODE=app1 RELEASE=restore-failure ./scripts/deploy.sh 2>&1)
restore_failure_exit=$?
set -e
test "${restore_failure_exit}" -ne 0
grep -q 'restore failed on app1' <<<"${restore_failure_output}"
grep -q 'failed to restore transaction restore-failure' <<<"${restore_failure_output}"

transaction_failure_start=$(wc -l <"${ssh_call_log}" | tr -d ' ')
set +e
(cd "${fixture_repo}" && SSH_CALL_LOG="${ssh_call_log}" FAIL_SWITCH_NODE=app2 \
  FAIL_MARKER="${fixture_repo}/.local/fail-marker" RELEASE=transaction-failure ./scripts/deploy.sh >/dev/null 2>&1)
transaction_exit=$?
set -e
test "${transaction_exit}" -ne 0
grep -q '^failed$' "${fixture_repo}/.local/deploy-transactions/transaction-failure.state"
tail -n "+$((transaction_failure_start + 1))" "${ssh_call_log}" >"${fixture_repo}/.local/transaction-failure-calls.log"
grep -q 'fixture-runtime-rollback' "${fixture_repo}/.local/transaction-failure-calls.log" || {
  echo "deploy did not restore runtime after a switching failure" >&2
  cat "${fixture_repo}/.local/transaction-failure-calls.log" >&2
  exit 1
}

# 目的はbranchに記録され、他のlaneは毎回gitから計算して引き継ぎ文へ書きます。
(cd "${fixture_repo}" && WORKTREE_PATH="${fixture_root}/other-lane" \
  ./scripts/worktree.sh optimize/other-lane "受取履歴を分離する" HEAD >/dev/null)
lane_output=$(cd "${fixture_repo}" && WORKTREE_PATH="${fixture_root}/phase1-obvious" \
  ./scripts/worktree.sh optimize/fixture-obvious "自明な改善を入れる" HEAD)
grep -q "^目的:     自明な改善を入れる$" <<<"${lane_output}"
grep -q "optimize/other-lane" <<<"${lane_output}"
(cd "${fixture_repo}" && WORKTREE_PATH="${fixture_root}/phase1-obvious" \
  ./scripts/worktree.sh optimize/fixture-obvious "自明な改善を入れる" HEAD >/dev/null)
test -f "${fixture_root}/phase1-obvious/webapp/rust/main.rs"
test "$(git -C "${fixture_repo}" config branch.optimize/fixture-obvious.description)" = "自明な改善を入れる"
grep -q "^- 目的: 自明な改善を入れる$" "${fixture_root}/phase1-obvious/.local/lane.md"
grep -q "optimize/other-lane" "${fixture_root}/phase1-obvious/.local/lane.md"

mkdir -p "${fixture_repo}/.local/inspection"
cat >"${fixture_repo}/.local/inspection/app1.json" <<'EOF'
{
  "node":"app1", "running_services":["nginx.service","mysql.service"],
  "processes":["1 root nginx","2 mysql mysqld"],
  "application_candidates":["/home/isucon/webapp/go.mod"],
  "application_candidate_ownership":["/home/isucon/webapp/go.mod\tisucon\tisucon"],
  "configuration_paths":["/etc/nginx/nginx.conf","/etc/mysql"],
  "service_fragments":["nginx.service\t/etc/systemd/system/nginx.service","isu-rust.service\t/etc/systemd/system/isu-rust.service"],
  "nginx_access_logs":["/var/log/nginx/custom.log","/var/log/nginx/isuscope-access.log"],
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
printf '[package]\nname = "isu-app"\nversion = "0.1.0"\n' >"${fixture_repo}/webapp/rust/Cargo.toml"
mkdir -p "${fixture_repo}/webapp/rust/src"
printf 'let cookie = Cookie::build("app_session", id);\nlet owner = jar.cookie("owner_session");\nconst SESSION_EXPIRES_KEY: &str = "EXPIRES";\n' >"${fixture_repo}/webapp/rust/src/session.rs"
(cd "${fixture_repo}" && ./scripts/configure-draft.sh)
jq -e '.items | any(.node_group == "role_mysql" and .source_node == "app1")' \
  "${fixture_repo}/.local/draft/sync.json" >/dev/null
# Rustを採用した場合、webapp全体ではなくRust実装・unit・小さいSQLだけを配布候補にします。
jq -e '.items | all(.name != "webapp")' "${fixture_repo}/.local/draft/sync.json" >/dev/null
jq -e '.items | any(.name == "rust-app" and .local == "webapp/rust" and .remote == "/home/isucon/webapp/rust" and .owner == "isucon")' \
  "${fixture_repo}/.local/draft/sync.json" >/dev/null
jq -e '.items | any(.name == "rust-service" and .remote == "/etc/systemd/system/isu-rust.service")' \
  "${fixture_repo}/.local/draft/sync.json" >/dev/null
jq -e '.items | any(.name == "sql-schema.sql" and .remote == "/home/isucon/webapp/sql/schema.sql")' \
  "${fixture_repo}/.local/draft/sync.json" >/dev/null
jq -e '.build_commands[0].item == "rust-app" and (.build_commands[0].command | contains("release/isu-app")) and (.build_commands[0].command | contains("replace-with") | not)' \
  "${fixture_repo}/.local/draft/sync.json" >/dev/null
jq -e '.post_deploy_commands | any(.command == "sudo systemctl restart isu-rust")' \
  "${fixture_repo}/.local/draft/sync.json" >/dev/null
# sessionを識別するnginx変数をコードから推定し、確認を促します。
jq -e '.observability_nginx_session_source == "$cookie_app_session$cookie_owner_session"' \
  "${fixture_repo}/.local/draft/ansible-vars.json" >/dev/null
jq -e '.warnings | any(test("session source guessed"))' "${fixture_repo}/.local/draft/summary.json" >/dev/null
jq -e '.post_deploy_commands | any(.node_group == "role_nginx" and .command == "sudo nginx -t")' \
  "${fixture_repo}/.local/draft/sync.json" >/dev/null
jq -e '.rollback_commands | any(.node_group == "role_nginx" and .command == "sudo systemctl reload nginx")' \
  "${fixture_repo}/.local/draft/sync.json" >/dev/null
jq -e '.ISUSCOPE_SERVICE_UNITS == "mysql.service nginx.service"' \
  "${fixture_repo}/.local/draft/isuscope.json" >/dev/null
(cd "${fixture_repo}" && CONFIRM_DRAFT=true ./scripts/configure-apply.sh)
(cd "${fixture_repo}" && ./scripts/discover.sh && ./scripts/sync-check.sh)
jq -e '.all.children.role_mysql.hosts | keys == ["app1"]' \
  "${fixture_repo}/.local/ansible-inventory.json" >/dev/null
grep -q 'log=/var/log/nginx/isuscope-access.log' "${fixture_repo}/.isuscope/config.toml"
grep -q '^service_units = \["mysql.service", "nginx.service"\]$' \
  "${fixture_repo}/.isuscope/config.toml"

echo "initial automation fixture passed"
