#!/usr/bin/env python3
"""終了前の確認（docs/guides/finish.md）。主催者の追試で落ちる原因を、問題に依らない形で先に確かめる。

  python3 scripts/final-verification.py check    # make final-check: 再起動しない確認
  python3 scripts/final-verification.py reboot   # make final-reboot: 全台を再起動して、戻った後と数分後を確かめる
  python3 scripts/final-verification.py db-late  # make final-db-late: DBが後から起動してもアプリが立ち直るか

追試は、全台の再起動（順序は保証されず、長く置かれることもある）→ envcheck → 最後の負荷走行の対象へのベンチ
（再現スコアが規定のしきい値を下回ればfail）→ もう一度再起動 → 書いたデータが残っているか → フロントエンド、
という流れで行われる。落ちる原因は、enableしていないservice、DBより先に起動して落ちるアプリ、起動完了を
通知できずに後から止められるDB、再起動で消える設定、追試の2回目のベンチで落ちるアプリ。問題ごとの値（envcheckのcommand、管理ユーザー、確かめるURL、インスタンス
種別）はconfig/final-check.jsonに書く。結果は1行ずつOK・WARN・FAIL・INFOで出し、FAILがあれば1で終わる。
"""
import argparse
import concurrent.futures
import importlib.util
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('isuscope_sql', Path(__file__).with_name('isuscope_sql.py'))
isuscope_sql = importlib.util.module_from_spec(spec)
spec.loader.exec_module(isuscope_sql)
LOCAL = ROOT / '.local'
CONFIG = ROOT / 'config/final-check.json'
SNAPSHOT = LOCAL / 'final-snapshot.json'
# どの問題でもservice単位で動くもの。あるnodeにだけあれば、そのnodeで確かめる。
MIDDLEWARE = ['nginx', 'mysql', 'mariadb', 'redis-server', 'memcached', 'postgresql']
UNIT_COMMAND = re.compile(r'systemctl\s+(?:--\S+\s+)*(?:is-active|is-enabled|enable|restart|start|reload)'
                          r'(?:\s+--\S+)*((?:\s+[A-Za-z0-9@_.-]+)+)')
NODE_TIMEOUT = 30
SYSCTL_DRIFT = r"""files=$(for dir in /usr/lib/sysctl.d /run/sysctl.d /etc/sysctl.d; do for file in "$dir"/*.conf; do [ -r "$file" ] && echo "${file##*/} $file"; done; done | awk '{path[$1] = $2} END {for (name in path) print name, path[name]}' | sort | cut -d' ' -f2)
[ -r /etc/sysctl.conf ] && files="$files /etc/sysctl.conf"
drift=$(awk '{line = $0; sub(/^[ \t]+/, "", line); if (line == "" || line ~ /^[#;]/) next; if (substr(line, 1, 1) == "-") line = substr(line, 2); at = index(line, "="); if (!at) next; key = substr(line, 1, at - 1); value = substr(line, at + 1); gsub(/[ \t]/, "", key); gsub(/^[ \t]+|[ \t]+$/, "", value); gsub(/[ \t]+/, " ", value); want[key] = value} END {for (key in want) print key "\t" want[key]}' $files 2>/dev/null | while IFS="$(printf '\t')" read -r key want; do
  case "$key" in (*'*'*) continue ;; esac
  now=$(sysctl -n "$key" 2>/dev/null | tr -s ' \t' ' ')
  [ -n "$now" ] && [ "$now" != "$want" ] && printf ' %s=%s(file:%s)' "$key" "$now" "$want"
done)
[ -n "$drift" ] && say WARN "sysctl differs from the boot-time files" "$drift" || say OK sysctl "matches the boot-time files"
"""


class Report:
    def __init__(self, quiet=False):
        self.lines = []
        self.quiet = quiet

    def add(self, level, scope, name, detail=''):
        self.lines.append((level, scope, name, detail))
        if not self.quiet:
            print(f'{level:<4} {scope:<12} {name}' + (f': {detail}' if detail else ''), flush=True)

    def failed(self):
        return any(level == 'FAIL' for level, *_ in self.lines)

    def summary(self):
        counts = {level: sum(1 for line in self.lines if line[0] == level) for level in ('FAIL', 'WARN', 'OK')}
        print(f"\n{counts['FAIL']} FAIL, {counts['WARN']} WARN, {counts['OK']} OK")


def load_config():
    config = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    config.setdefault('http_checks', [])
    config.setdefault('min_score_ratio', 0.85)
    config.setdefault('settle_seconds', 300)
    config.setdefault('reboot_timeout_seconds', 600)
    return config


def inventory():
    path = Path(os.environ.get('ANSIBLE_INVENTORY', LOCAL / 'ansible-inventory.json'))
    groups = json.loads(path.read_text())['all']['children']
    return {name: list((group.get('hosts') or {}).keys()) for name, group in groups.items()}


def environment():
    values = {}
    path = LOCAL / 'environment.env'
    if path.exists():
        for line in path.read_text().splitlines():
            match = re.match(r'^([A-Z_]+)=(.*)$', line.strip())
            if match:
                values[match.group(1)] = match.group(2).strip().strip('\'"')
    return values


def ssh(node, script, timeout=NODE_TIMEOUT):
    """1回のSSHで`script`を流す。固まったnodeの古い接続に乗らないよう、新しく張る。"""
    env = dict(os.environ, SSH_CONTROL_PERSIST='0')
    try:
        done = subprocess.run([str(ROOT / 'scripts/ssh-node.sh'), node, script], env=env,
                              capture_output=True, text=True, timeout=timeout)
        return done.returncode, done.stdout, done.stderr
    except subprocess.TimeoutExpired:
        return None, '', f'no answer within {timeout}s'
    except OSError as error:
        return None, '', str(error)


def on_nodes(nodes, work):
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(nodes))) as pool:
        return dict(zip(nodes, pool.map(work, nodes)))


def unit_names(config):
    """確かめるunit。設定のunits、sync.jsonのsystemctlの行、config/systemdのunit、よくあるmiddleware。"""
    names = list(config.get('units', []))
    sync = ROOT / 'config/sync.json'
    if sync.exists():
        manifest = json.loads(sync.read_text())
        for key in ('status_commands', 'post_deploy_commands'):
            for entry in manifest.get(key, []):
                command = entry if isinstance(entry, str) else entry.get('command', '')
                for match in UNIT_COMMAND.finditer(command):
                    names.extend(match.group(1).split())
    systemd = ROOT / 'config/systemd'
    if systemd.is_dir():
        names.extend(path.name for path in sorted(systemd.iterdir())
                     if path.suffix in ('.service', '.timer', '.socket'))
    names.extend(MIDDLEWARE)
    names.extend(config.get('protected_units', []))
    unique = []
    for name in names:
        name = name if '.' in name else name + '.service'
        if name not in unique:
            unique.append(name)
    return unique


def node_script(config, units, http, envcheck=False, settled=False):
    """nodeで流すshell。1行ずつ`level<TAB>name<TAB>detail`を出す。どの行も10秒で打ち切る。"""
    quoted = ' '.join(shlex.quote(unit) for unit in units)
    lines = [
        't() { if command -v timeout >/dev/null 2>&1; then timeout 10 "$@"; else "$@"; fi; }',
        'say() { printf "%s\\t%s\\t%s\\n" "$1" "$2" "$3"; }',
        # unit: 動いているのに自動起動しない、自動起動するのに動いていない、起動の途中で止まっている。
        f'for unit in {quoted}; do',
        '  t systemctl cat "$unit" >/dev/null 2>&1 || continue',
        '  enabled=$(t systemctl is-enabled "$unit" 2>/dev/null); active=$(t systemctl is-active "$unit" 2>/dev/null)',
        '  kind=$(t systemctl show -p Type --value "$unit" 2>/dev/null); result=$(t systemctl show -p Result --value "$unit" 2>/dev/null)',
        '  case "$enabled:$active" in',
        '    enabled:active|static:active|alias:active|indirect:active|enabled-runtime:active) say OK "unit $unit" "$enabled, $active" ;;',
        '    enabled:inactive) if [ "$kind" = oneshot ] && [ "$result" = success ]; then say OK "unit $unit" "oneshot, succeeded"; else say FAIL "unit $unit" "enabled but inactive ($result): it starts after a reboot; disable it if it should stay off"; fi ;;',
        '    disabled:active|masked:active) say FAIL "unit $unit" "$active but $enabled: it will not start after a reboot (systemctl enable it)" ;;',
        '    *:activating|*:deactivating|*:failed|enabled:*) say FAIL "unit $unit" "$enabled, $active ($result)" ;;',
        '    *) ;;',
        '  esac',
        'done',
        'if failed=$(t systemctl --failed --no-legend --plain 2>/dev/null); then',
        '  failed=$(printf "%s" "$failed" | awk \'{print $1}\' | paste -sd" " -)',
        '  [ -n "$failed" ] && say FAIL "failed units" "$failed" || say OK "failed units" none',
        'else say FAIL "failed units" "could not query systemd"; fi',
        'if stuck=$(t systemctl list-jobs --no-legend 2>/dev/null); then',
        '  stuck=$(printf "%s" "$stuck" | awk \'{print $2 " " $3}\' | paste -sd", " -)',
        '  [ -n "$stuck" ] && say ' + ('FAIL' if settled else 'WARN') + ' "pending systemd jobs" "$stuck"',
        'else say FAIL "pending systemd jobs" "could not query systemd"; fi',
        # 計測と調査用のprocessは外す（追試の再現スコアを下げる）。
        '[ -f /etc/nginx/conf.d/00-isuscope-log.conf ] && say WARN "nginx LTSV access log" "still on; set observability_nginx_ltsv false and rerun bootstrap with --tags observability_nginx_log"',
        'if command -v nginx >/dev/null 2>&1; then out=$(t sudo -n nginx -t 2>&1) && say OK "nginx -t" ok || say FAIL "nginx -t" "$(printf "%s" "$out" | tail -n 1)"; fi',
        'if systemctl is-active -q mysql 2>/dev/null || systemctl is-active -q mariadb 2>/dev/null; then',
        '  slow=$(t sudo -n mysql -N -e "SELECT @@slow_query_log" 2>/dev/null)',
        '  case "$slow" in 1) say WARN "mysql slow log" "on (also check that the setting is not only SET GLOBAL)";; 0) say OK "mysql slow log" off;; *) say WARN "mysql slow log" "could not ask mysql";; esac',
        'fi',
        # このshell自身（command lineに探す語を含む）は除く。
        'probes=$(pgrep -af "perf record|offcputime|bpftrace|isuscope-[0-9a-f]{8}|pprof" 2>/dev/null | grep -v -e pgrep -e "say()" | cut -c1-80 | paste -sd"; " -)',
        '[ -n "$probes" ] && say WARN "measurement processes" "$probes" || say OK "measurement processes" none',
        # Goのアプリのprofileの窓口（profile.go）。ISUCON_PROFILE_ADDRを空にすると聞かない。
        'if command -v ss >/dev/null 2>&1; then listen=$(ss -Hltn "sport = :6060" 2>/dev/null | head -n 1); '
        '[ -n "$listen" ] && say WARN "Go profile endpoint" "port 6060 still listens; set ISUCON_PROFILE_ADDR= (empty) in the app EnvironmentFile" '
        '|| say OK "Go profile endpoint" off; fi',
        # 再起動で消える設定: 起動時にsystemd-sysctlが入れる値（file名の順で後のものが勝つ）と、今の値が違う。
        SYSCTL_DRIFT,
        'used=$(df -P / | awk \'NR==2 {sub("%", "", $5); print $5}\')',
        'if [ "$used" -ge 95 ]; then say FAIL disk "/ ${used}% used"; elif [ "$used" -ge 85 ]; then say WARN disk "/ ${used}% used"; else say OK disk "/ ${used}% used"; fi',
        'free -m | awk \'NR==2 {level = ($7 < $2 / 10) ? "WARN" : "OK"; printf "%s\\tmemory\\t%s of %s MiB available\\n", level, $7, $2}\'',
    ]
    for user in config.get('admin_users', []):
        quoted_user = shlex.quote(user)
        lines.append(f'if id {quoted_user} >/dev/null 2>&1 && t sudo -n sh -c "test -s ~{user}/.ssh/authorized_keys"; '
                     f'then say OK "admin user {user}" "exists with authorized_keys"; '
                     f'else say FAIL "admin user {user}" "missing or without ~/.ssh/authorized_keys"; fi')
    for unit in config.get('protected_units', []):
        quoted_unit = shlex.quote(unit)
        lines.append(f'[ "$(t systemctl is-enabled {quoted_unit} 2>/dev/null)" = enabled ] && say OK "protected {unit}" enabled '
                     f'|| say FAIL "protected {unit}" "not enabled or missing"')
    for check in http:
        command = ['curl', '-sk', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '5']
        if check.get('host'):
            command += ['-H', f"Host: {check['host']}"]
        command.append(check['url'])
        expect = str(check.get('expect_status', 200))
        lines.append(f'code=$({" ".join(shlex.quote(part) for part in command)} 2>/dev/null); '
                     f'[ "$code" = {expect} ] && say OK "http {check["url"]}" "$code" '
                     f'|| say FAIL "http {check["url"]}" "got ${{code:-no answer}}, expected {expect} (without POST /initialize)"')
    if envcheck and config.get('envcheck_command'):
        lines.append(f'out=$(t {config["envcheck_command"]} 2>&1) && say OK envcheck ok '
                     '|| say FAIL envcheck "$(printf "%s" "$out" | tail -n 2 | tr "\\n" " " | cut -c1-200)"')
    lines.extend(['printf "ISUSCOPE_FINAL_CHECK_COMPLETE\\n"', 'exit 0'])
    return '\n'.join(lines) + '\n'


def http_for(config, groups, node):
    checks = []
    for check in config['http_checks']:
        group = check.get('node_group', 'application')
        members = groups.get(group) or groups.get('application', [])
        if node in members:
            checks.append(check)
    return checks


def check_nodes(report, config, groups, nodes, envcheck=False, settled=False):
    units = unit_names(config)

    def work(node):
        return ssh(node, node_script(config, units, http_for(config, groups, node), envcheck, settled))

    for node, (code, out, err) in on_nodes(nodes, work).items():
        if code != 0:
            report.add('FAIL', node, 'node checks', command_error(code, out, err))
        received = 0
        for line in out.splitlines():
            parts = line.split('\t', 2)
            if len(parts) == 3 and parts[0] in ('OK', 'WARN', 'FAIL', 'INFO'):
                report.add(parts[0], node, parts[1], parts[2])
                received += 1
        if not received or 'ISUSCOPE_FINAL_CHECK_COMPLETE' not in out.splitlines():
            report.add('FAIL', node, 'node checks', 'missing or incomplete diagnostic output')


def command_error(code, out, err):
    detail = (err.strip().splitlines() or out.strip().splitlines() or ['no output'])[-1]
    return f'exit {code if code is not None else "timeout/unavailable"}: {detail[:240]}'


def check_local(report, config, nodes):
    git = lambda *args: subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True)
    dirty = git('status', '--porcelain').stdout.strip()
    report.add('WARN' if dirty else 'OK', 'local', 'git status', 'uncommitted changes' if dirty else 'clean')
    ahead = git('rev-list', '--count', '@{u}..HEAD')
    if ahead.returncode != 0:
        report.add('WARN', 'local', 'git push', 'no upstream branch')
    else:
        count = int(ahead.stdout.strip() or 0)
        report.add('WARN' if count else 'OK', 'local', 'git push', f'{count} commits not pushed' if count else 'pushed')
    deployed = subprocess.run([str(ROOT / 'scripts/check-deployed-commit.sh')], cwd=ROOT, capture_output=True, text=True)
    report.add('OK' if deployed.returncode == 0 else 'FAIL', 'local', 'deployed commit',
               (deployed.stdout + deployed.stderr).strip().splitlines()[-1] if (deployed.stdout + deployed.stderr).strip() else '')
    check_score(report, config)
    check_aws(report, config, nodes)


def final_runs(root=None):
    """Runs whose benchmark started and finished, oldest first, with isuscope's configuration key."""
    return isuscope_sql.rows(root or ROOT, "SELECT id, score, passed = 1 AS passed, config_key FROM runs "
                             "WHERE benchmark_started = 1 AND state NOT IN ('running', 'aborted') "
                             "ORDER BY started_at, id")


def check_score(report, config):
    """The last run is the final score; the re-test is compared with it. The runs that count are those of
    the last run's configuration (isuscope's config_key), so committing run records after them changes nothing."""
    try:
        runs = final_runs()
    except isuscope_sql.Unavailable as error:
        report.add('FAIL', 'isuscope', 'final runs', f'isuscope sql failed: {error}')
        return
    if not runs:
        report.add('WARN', 'isuscope', 'final runs', 'no runs')
        return
    last = runs[-1]
    report.add('OK' if last['passed'] else 'FAIL', 'isuscope', 'last run',
               f'{"PASS" if last["passed"] else "FAIL"} score {last["score"]} (run {last["id"][-8:]})')
    same = [run for run in runs if run['config_key'] and run['config_key'] == last['config_key'] and run['passed']]
    if len(same) < 3:
        report.add('WARN', 'isuscope', 'runs of the final configuration',
                   f'{len(same)} passed runs of the last run\'s configuration; run 3 (the third after make final-reboot)')
    ratio_needed = config.get('min_score_ratio')
    if same and last['score'] and ratio_needed:
        worst = min(run['score'] or 0 for run in same)
        ratio = worst / last['score']
        report.add('OK' if ratio >= ratio_needed else 'FAIL', 'isuscope', 'reproduction',
                   f'worst {worst} is {ratio:.0%} of the last {last["score"]} over {len(same)} runs of the final '
                   f'configuration (needs {ratio_needed:.0%})')


def check_aws(report, config, nodes):
    discovered = LOCAL / 'discovered-nodes.json'
    env = environment()
    if not discovered.exists() or not env.get('AWS_PROFILE'):
        report.add('INFO', 'aws', 'instances', 'not checked (no .local/discovered-nodes.json or AWS_PROFILE)')
        return
    entries = [entry for entry in json.loads(discovered.read_text())
               if entry.get('group') != 'benchmark' and entry.get('instance_id')]
    if not entries:
        report.add('INFO', 'aws', 'instances', 'no EC2 instances among the nodes')
        return
    done = subprocess.run(['aws', '--profile', env['AWS_PROFILE'], '--region', env.get('AWS_REGION', 'ap-northeast-1'),
                           'ec2', 'describe-instances', '--instance-ids', *[entry['instance_id'] for entry in entries],
                           '--output', 'json'], capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        report.add('WARN', 'aws', 'instances', 'describe-instances failed: ' + done.stderr.strip()[:160])
        return
    instances = [instance for reservation in json.loads(done.stdout)['Reservations']
                 for instance in reservation['Instances']]
    stopped = [f"{instance['InstanceId']}={instance['State']['Name']}" for instance in instances
               if instance['State']['Name'] != 'running']
    report.add('FAIL' if stopped else 'OK', 'aws', 'all running',
               ' '.join(stopped) if stopped else f'{len(instances)} instances running (a stopped one at the end disqualifies)')
    vpcs = sorted({instance.get('VpcId', '') for instance in instances})
    report.add('OK' if len(vpcs) == 1 else 'FAIL', 'aws', 'one VPC', ' '.join(vpcs))
    if config.get('instance_count'):
        count = len(instances)
        report.add('OK' if count == config['instance_count'] else 'FAIL', 'aws', 'instance count',
                   f'{count} (manual: {config["instance_count"]})')
    if config.get('instance_type'):
        wrong = [f"{instance['InstanceId']}={instance['InstanceType']}" for instance in instances
                 if instance['InstanceType'] != config['instance_type']]
        report.add('FAIL' if wrong else 'OK', 'aws', 'instance type',
                   ' '.join(wrong) if wrong else config['instance_type'])
    groups = sorted({group['GroupId'] for instance in instances for group in instance.get('SecurityGroups', [])})
    report.add('INFO', 'aws', 'security groups', ' '.join(groups) + ' (compare their rules with the manual)')


def db_nodes(report, nodes):
    script = r"""for unit in mysql mariadb; do
  state=$(systemctl is-active "$unit" 2>/dev/null)
  code=$?
  case "$state:$code" in
    active:0) printf 'db\t%s\n' "$unit"; exit 0 ;;
    inactive:3|inactive:4|unknown:3|unknown:4) ;;
    *) echo "cannot identify database: $unit is $state (exit $code)" >&2; exit 1 ;;
  esac
done
printf 'no-db\n'
"""
    databases = {}
    for node, (code, out, err) in on_nodes(nodes, lambda node: ssh(node, script)).items():
        if code == 0 and out.strip() in ('db\tmysql', 'db\tmariadb'):
            databases[node] = out.strip().split('\t')[1]
        elif code == 0 and out.strip() == 'no-db':
            report.add('INFO', node, 'database', 'no active mysql or mariadb')
        else:
            report.add('FAIL', node, 'database discovery', command_error(code, out, err))
    return databases


COUNT_SCRIPT = r"""
t() { if command -v timeout >/dev/null 2>&1; then timeout 60 "$@"; else "$@"; fi; }
query=$(t sudo -n mysql -N -e "SET SESSION group_concat_max_len = 1048576; SELECT GROUP_CONCAT(CONCAT('SELECT ''', table_schema, '.', table_name, ''', COUNT(*) FROM \`', table_schema, '\`.\`', table_name, '\`') SEPARATOR ' UNION ALL ') FROM information_schema.tables WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')") || exit $?
if [ "$query" = NULL ]; then
  printf 'ISUSCOPE_TABLE_COUNTS_EMPTY\n'
else
  [ -n "$query" ] || exit 1
  t sudo -n mysql -N -e "$query" || exit $?
fi
printf 'ISUSCOPE_TABLE_COUNTS_COMPLETE\n'
"""


def table_counts(report, nodes):
    counts = {}
    for node, (code, out, err) in on_nodes(nodes, lambda node: ssh(node, COUNT_SCRIPT, timeout=120)).items():
        counts[node] = None
        lines = out.splitlines()
        if code != 0 or not lines or lines[-1] != 'ISUSCOPE_TABLE_COUNTS_COMPLETE':
            report.add('FAIL', node, 'table counts', command_error(code, out, err))
            continue
        if lines[:-1] == ['ISUSCOPE_TABLE_COUNTS_EMPTY']:
            counts[node] = {}
            report.add('INFO', node, 'table counts', 'no application tables')
            continue
        tables = {}
        for line in lines[:-1]:
            parts = line.split('\t')
            if len(parts) != 2 or not parts[0] or not parts[1].isdigit() or parts[0] in tables:
                break
            tables[parts[0]] = int(parts[1])
        else:
            if tables:
                counts[node] = tables
                continue
        report.add('FAIL', node, 'table counts', 'missing or malformed row counts')
    return counts


def boot_ids(nodes):
    return {node: out.strip() for node, (code, out, _) in
            on_nodes(nodes, lambda node: ssh(node, 'cat /proc/sys/kernel/random/boot_id', timeout=15)).items()}


def wait_for_boot(report, nodes, before, timeout):
    deadline = time.monotonic() + timeout
    waiting = set(nodes)
    while waiting and time.monotonic() < deadline:
        for node, booted in boot_ids(sorted(waiting)).items():
            if booted and booted != before.get(node):
                ssh(node, 'command -v timeout >/dev/null && timeout 120 systemctl is-system-running --wait >/dev/null 2>&1; true',
                    timeout=150)
                report.add('OK', node, 'rebooted', f'back after {int(timeout - (deadline - time.monotonic()))}s')
                waiting.discard(node)
        if waiting:
            time.sleep(5)
    for node in sorted(waiting):
        report.add('FAIL', node, 'rebooted', f'did not come back within {timeout}s (look at the instance from the cloud console)')
    return [node for node in nodes if node not in waiting]


def compare_counts(report, before, after):
    for node, tables in before.items():
        if tables is None or after.get(node) is None:
            report.add('FAIL', node, 'data after reboot', 'row counts unavailable; cannot compare')
            continue
        missing = sorted(set(tables) - set(after[node]))
        if missing:
            report.add('FAIL', node, 'data after reboot', 'tables missing: ' + ', '.join(missing[:8]))
            continue
        lost = [f'{table} {count}->{after.get(node, {}).get(table, 0)}' for table, count in tables.items()
                if after.get(node, {}).get(table, 0) < count]
        if lost:
            report.add('FAIL', node, 'data after reboot', 'rows lost: ' + ', '.join(lost[:8]))
        elif tables:
            report.add('OK', node, 'data after reboot', f'{len(tables)} tables, no table lost rows')


def node_commands(report, commands, name, timeout):
    """Collect every node's result, including a failure on another node."""
    def work(node):
        try:
            return ssh(node, commands[node], timeout=timeout)
        except Exception as error:
            return None, '', str(error)

    results = on_nodes(list(commands), work)
    for node, (code, out, err) in results.items():
        report.add('OK' if code == 0 else 'FAIL', node, name,
                   out.strip() if code == 0 else command_error(code, out, err))
    return results


def db_late(report, config, groups, nodes):
    databases = db_nodes(report, nodes)
    if report.failed():
        return
    if not databases:
        report.add('FAIL', 'db-late', 'database', 'no active mysql or mariadb; test not performed')
        return
    protected = {unit if '.' in unit else unit + '.service' for unit in config.get('protected_units', [])}
    units = [unit for unit in unit_names(config)
             if unit.endswith('.service') and unit.removesuffix('.service') not in MIDDLEWARE
             and unit not in protected]
    if not units:
        report.add('FAIL', 'db-late', 'app units', 'no application services to restart')
        return
    restart = ' '.join(shlex.quote(unit) for unit in units)
    try:
        stopped = node_commands(report, {
            node: f'sudo -n systemctl stop {unit} || exit $?; '
                  f'state=$(systemctl is-active {unit}); '
                  '[ "$state" = inactive ] || { echo "database is $state" >&2; exit 1; }'
            for node, unit in databases.items()
        }, 'stop database', 120)
        if any(code != 0 for code, _, _ in stopped.values()):
            return
        restarted = node_commands(report, {
            node: f'for unit in {restart}; do '
                  'if systemctl is-enabled -q "$unit" 2>/dev/null; then '
                  'sudo -n systemctl restart "$unit" || exit $?; '
                  'printf "restarted\\t%s\\n" "$unit"; fi; done; '
                  'printf "ISUSCOPE_APP_RESTART_COMPLETE\\n"'
            for node in nodes
        }, 'restart app without database', 120)
        tested = False
        for node, (code, out, _) in restarted.items():
            if code == 0 and 'ISUSCOPE_APP_RESTART_COMPLETE' not in out.splitlines():
                report.add('FAIL', node, 'restart app without database', 'incomplete restart output')
            tested |= any(line.startswith('restarted\t') for line in out.splitlines())
        if not tested:
            report.add('FAIL', 'db-late', 'app units', 'no enabled application service was restarted')
        if report.failed():
            return
        time.sleep(15)
    except (Exception, KeyboardInterrupt) as error:
        report.add('FAIL', 'db-late', 'interrupted', str(error) or type(error).__name__)
    finally:
        # Even a failed/timed-out stop may have stopped the DB remotely. Restore every
        # database that was active before the test, and keep failures visible.
        node_commands(report, {
            node: f'sudo -n systemctl start {unit} && systemctl is-active --quiet {unit}'
            for node, unit in databases.items()
        }, 'restore database', 180)
    if report.failed():
        return
    deadline = time.monotonic() + 90
    while True:
        attempt = Report(quiet=True)
        check_nodes(attempt, config, groups, nodes, envcheck=False)
        if not attempt.failed() or time.monotonic() > deadline:
            break
        time.sleep(10)
    for line in attempt.lines:
        report.add(*line)


def emit_progress(state, message):
    subprocess.run([sys.executable, str(ROOT / 'scripts/progress.py'), 'emit', 'final', state, message],
                   cwd=ROOT, capture_output=True)


def lock_or_exec():
    """再起動とDBの停止は、deployやベンチと重ならないよう操作lockの中で行う。"""
    if os.environ.get('ISUSCOPE_LOCK_HELD') != '1':
        os.execvp('isuscope', ['isuscope', 'lock', '--path', str(LOCAL / 'operation.lock'), '--',
                               sys.executable, *sys.argv])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['check', 'reboot', 'db-late', 'settled'])
    parser.add_argument('--nodes', help='settled: 確かめるnode（reboot が渡す）')
    parser.add_argument('--settle', type=int, help='再起動の後、もう一度確かめるまでの秒数（既定は設定のsettle_seconds）')
    parser.add_argument('--yes', action='store_true', help='全台を再起動する・DBを止めることの確認を省く')
    args = parser.parse_args()
    config = load_config()
    try:
        groups = inventory()
    except (OSError, ValueError, KeyError):
        print('no node inventory (.local/ansible-inventory.json); run make discover first', file=sys.stderr)
        return 2
    nodes = groups.get('application', [])
    report = Report()
    if not nodes:
        report.add('FAIL', 'inventory', 'application nodes', 'no nodes to check')
        report.summary()
        return 1
    if args.command in ('reboot', 'db-late'):
        if not args.yes:
            print(f'{args.command} touches every node ({" ".join(nodes)}); rerun with --yes', file=sys.stderr)
            return 2
        lock_or_exec()

    if args.command == 'check':
        check_local(report, config, nodes)
        check_nodes(report, config, groups, nodes, envcheck=False)
    elif args.command == 'reboot':
        databases = db_nodes(report, nodes)
        before = table_counts(report, list(databases))
        if report.failed():
            report.summary()
            return 1
        SNAPSHOT.write_text(json.dumps({'taken_at': time.time(), 'counts': before}, indent=2))
        report.add('INFO', 'reboot', 'snapshot', f'row counts of {sum(len(t) for t in before.values())} tables on {" ".join(databases) or "no DB node"}')
        booted = boot_ids(nodes)
        # 全台を同時に。主催者は順序を保証しないので、どの順に戻ってもよいことを確かめる。
        on_nodes(nodes, lambda node: ssh(node, 'sudo -n systemctl reboot', timeout=15))
        report.add('INFO', 'reboot', 'reboot', 'sent sudo systemctl reboot to ' + ' '.join(nodes))
        time.sleep(config.get('reboot_grace_seconds', 10))
        # 戻らないnodeがあっても、戻ったnodeは確かめる。
        back = wait_for_boot(report, nodes, booted, config['reboot_timeout_seconds'])
        if back:
            check_nodes(report, config, groups, back, envcheck=True)
            returned = [node for node in databases if node in back]
            compare_counts(report, {node: before[node] for node in returned}, table_counts(report, returned))
            settle = config['settle_seconds'] if args.settle is None else args.settle
            if settle > 0:
                # The later check only reads, so it waits outside the operation lock: benchmarks and
                # deploys need not wait for it. Its result comes as a progress line.
                with (LOCAL / 'final-settle.log').open('w') as out:
                    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'settled', '--settle', str(settle),
                                      '--nodes', ','.join(back)], cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, start_new_session=True,
                                     env={k: v for k, v in os.environ.items() if k != 'ISUSCOPE_LOCK_HELD'})
                report.add('INFO', 'reboot', 'settle', f'checking again in {settle}s in the background (units that fail '
                           'late, like a DB stopped after its start timeout); the result comes as `final settled` or '
                           '`final failed` in .local/progress.log, details in .local/final-settle.log')
    elif args.command == 'settled':
        time.sleep(args.settle or 0)
        check_nodes(report, config, groups, [n for n in (args.nodes or '').split(',') if n] or nodes, envcheck=False, settled=True)
        failed = [line for line in report.lines if line[0] == 'FAIL']
        emit_progress('failed' if failed else 'settled',
                      f'after {args.settle}s: ' + ('; '.join(f'{line[1]} {line[2]}: {line[3]}' for line in failed)[:300]
                                                  if failed else 'every unit still up'))
    elif args.command == 'db-late':
        def interrupted(signum, frame):
            raise KeyboardInterrupt(f'interrupted by signal {signum}')

        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            db_late(report, config, groups, nodes)
        finally:
            signal.signal(signal.SIGTERM, previous)
    report.summary()
    return 1 if report.failed() else 0


if __name__ == '__main__':
    sys.exit(main())
