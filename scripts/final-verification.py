#!/usr/bin/env python3
"""終了前の確認（docs/guides/finish.md）。主催者の追試で落ちる原因を、問題に依らない形で先に確かめる。

  python3 scripts/final-verification.py check    # make final-check: 再起動しない確認
  python3 scripts/final-verification.py reboot   # make final-reboot: 全台を再起動して、戻った後と数分後を確かめる
  python3 scripts/final-verification.py db-late  # make final-db-late: DBが後から起動してもアプリが立ち直るか

ISUCON9〜14の追試は、全台の再起動（順序は保証されない。poweroffの後10分以上置いた回もある）→ envcheck →
最後の負荷走行の対象へのベンチ（再現スコアが75〜85%以下ならfail）→ もう一度再起動 → 書いたデータが
残っているか → フロントエンド、だった。実際の失格・事故は、enableしていないservice、DBより先に起動して
落ちるアプリ、15分後に止められたDB（起動完了を通知できなかった）、再起動で消える設定、追試の2回目の
ベンチで落ちるアプリ、だった。問題ごとの値（envcheckのcommand、管理ユーザー、確かめるURL、インスタンス
種別）はconfig/final-check.jsonに書く。結果は1行ずつOK・WARN・FAIL・INFOで出し、FAILがあれば1で終わる。
"""
import argparse
import concurrent.futures
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
  case "$key" in *'*'*) continue ;; esac
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
        'failed=$(t systemctl --failed --no-legend --plain 2>/dev/null | awk \'{print $1}\' | paste -sd" " -)',
        '[ -n "$failed" ] && say FAIL "failed units" "$failed" || say OK "failed units" none',
        'stuck=$(t systemctl list-jobs --no-legend 2>/dev/null | awk \'{print $2 " " $3}\' | paste -sd", " -)',
        '[ -n "$stuck" ] && say ' + ('FAIL' if settled else 'WARN') + ' "pending systemd jobs" "$stuck"',
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
        if code is None or (code == 255 and not out):
            report.add('FAIL', node, 'ssh', (err.strip().splitlines() or ['no answer'])[-1])
            continue
        for line in out.splitlines():
            parts = line.split('\t', 2)
            if len(parts) == 3 and parts[0] in ('OK', 'WARN', 'FAIL', 'INFO'):
                report.add(parts[0], node, parts[1], parts[2])


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
    scout = subprocess.run([sys.executable, str(ROOT / 'scripts/scout/main.py'), 'status'], cwd=ROOT,
                           capture_output=True, text=True)
    try:
        running = json.loads(scout.stdout).get('daemon_running')
        report.add('WARN' if running else 'OK', 'local', 'scout', 'still running (make scout-stop)' if running else 'stopped')
    except ValueError:
        report.add('INFO', 'local', 'scout', 'status unavailable')
    check_score(report, config)
    check_aws(report, config, nodes)


def check_score(report, config):
    """最終スコアは最後の負荷走行（13・14）。追試はその75〜85%を下回るとfail。同じcommitのrunの振れで見る。"""
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    query = ("SELECT commit_hash, score, passed FROM runs WHERE state NOT IN ('aborted', 'running') "
             "ORDER BY started_at DESC LIMIT 20")
    done = subprocess.run(['isuscope', 'sql', query], cwd=ROOT, capture_output=True, text=True)
    if done.returncode != 0:
        report.add('WARN', 'isuscope', 'final runs', 'cannot read the runs: ' + done.stderr.strip()[:120])
        return
    rows = json.loads(done.stdout).get('rows', [])
    if not rows:
        report.add('WARN', 'isuscope', 'final runs', 'no runs')
        return
    last_commit, last_score, last_passed = rows[0]
    report.add('OK' if last_passed else 'FAIL', 'isuscope', 'last run',
               f'{"PASS" if last_passed else "FAIL"} score {last_score} (the last run is the final score in recent contests)')
    same = [row for row in rows if row[0] == head and row[2]]
    if len(same) < 3:
        report.add('WARN', 'isuscope', 'runs at HEAD', f'{len(same)} passed runs at {head[:12]}; run 3 (the third after make final-reboot)')
    if same and last_score:
        worst = min(row[1] or 0 for row in same)
        ratio = worst / last_score
        level = 'OK' if ratio >= config['min_score_ratio'] else 'FAIL'
        report.add(level, 'isuscope', 'reproduction',
                   f'worst {worst} is {ratio:.0%} of the last {last_score} over {len(same)} runs at HEAD '
                   f'(needs {config["min_score_ratio"]:.0%})')


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


def db_nodes(nodes):
    script = 'systemctl is-active -q mysql 2>/dev/null || systemctl is-active -q mariadb 2>/dev/null && echo db'
    return [node for node, (code, out, _) in on_nodes(nodes, lambda node: ssh(node, script)).items() if 'db' in out]


COUNT_SCRIPT = r"""
t() { if command -v timeout >/dev/null 2>&1; then timeout 60 "$@"; else "$@"; fi; }
query=$(t sudo -n mysql -N -e "SET SESSION group_concat_max_len = 1048576; SELECT GROUP_CONCAT(CONCAT('SELECT ''', table_schema, '.', table_name, ''', COUNT(*) FROM \`', table_schema, '\`.\`', table_name, '\`') SEPARATOR ' UNION ALL ') FROM information_schema.tables WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')" 2>/dev/null)
[ -n "$query" ] && [ "$query" != NULL ] && t sudo -n mysql -N -e "$query"
"""


def table_counts(nodes):
    counts = {}
    for node, (code, out, _) in on_nodes(nodes, lambda node: ssh(node, COUNT_SCRIPT, timeout=120)).items():
        counts[node] = {}
        for line in out.splitlines():
            parts = line.split('\t')
            if len(parts) == 2 and parts[1].isdigit():
                counts[node][parts[0]] = int(parts[1])
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
        lost = [f'{table} {count}->{after.get(node, {}).get(table, 0)}' for table, count in tables.items()
                if after.get(node, {}).get(table, 0) < count]
        if lost:
            report.add('FAIL', node, 'data after reboot', 'rows lost: ' + ', '.join(lost[:8]))
        elif tables:
            report.add('OK', node, 'data after reboot', f'{len(tables)} tables, no table lost rows')


def lock_or_exec():
    """再起動とDBの停止は、deployやベンチと重ならないよう操作lockの中で行う。"""
    if os.environ.get('ISUSCOPE_LOCK_HELD') != '1':
        os.execvp('isuscope', ['isuscope', 'lock', '--path', str(LOCAL / 'operation.lock'), '--',
                               sys.executable, *sys.argv])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['check', 'reboot', 'db-late'])
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
    if args.command in ('reboot', 'db-late'):
        if not args.yes:
            print(f'{args.command} touches every node ({" ".join(nodes)}); rerun with --yes', file=sys.stderr)
            return 2
        lock_or_exec()

    if args.command == 'check':
        check_local(report, config, nodes)
        check_nodes(report, config, groups, nodes, envcheck=False)
    elif args.command == 'reboot':
        databases = db_nodes(nodes)
        before = table_counts(databases)
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
            compare_counts(report, {node: before[node] for node in returned}, table_counts(returned))
            settle = config['settle_seconds'] if args.settle is None else args.settle
            if settle > 0:
                report.add('INFO', 'reboot', 'settle', f'checking again in {settle}s (units that fail late, like a DB stopped after its start timeout)')
                time.sleep(settle)
                check_nodes(report, config, groups, back, envcheck=False, settled=True)
    elif args.command == 'db-late':
        # 主催者は再起動の順序を保証しない。DBが後から上がっても、アプリが自分で立ち直るか。
        databases = db_nodes(nodes)
        if not databases:
            report.add('WARN', 'db-late', 'database', 'no node runs mysql or mariadb')
        else:
            units = [unit for unit in unit_names(config)
                     if unit.removesuffix('.service') not in MIDDLEWARE and unit not in config.get('protected_units', [])]
            on_nodes(databases, lambda node: ssh(
                node, 'sudo -n systemctl stop mysql 2>/dev/null || sudo -n systemctl stop mariadb', timeout=120))
            report.add('INFO', 'db-late', 'stopped', 'the database on ' + ' '.join(databases))
            restart = ' '.join(shlex.quote(unit) for unit in units)
            on_nodes(nodes, lambda node: ssh(
                node, f'for unit in {restart}; do systemctl is-enabled -q "$unit" 2>/dev/null && '
                      f'sudo -n systemctl restart "$unit"; done; true', timeout=120))
            report.add('INFO', 'db-late', 'restarted', 'the app units while the database is down: ' + ' '.join(units))
            time.sleep(15)
            on_nodes(databases, lambda node: ssh(
                node, 'sudo -n systemctl start mysql 2>/dev/null || sudo -n systemctl start mariadb', timeout=180))
            report.add('INFO', 'db-late', 'started', 'the database again; the apps must recover without a restart')
            deadline = time.monotonic() + 90
            while True:
                attempt = Report(quiet=True)
                check_nodes(attempt, config, groups, nodes, envcheck=False)
                if not attempt.failed() or time.monotonic() > deadline:
                    break
                time.sleep(10)
            for line in attempt.lines:
                report.add(*line)
    report.summary()
    return 1 if report.failed() else 0


if __name__ == '__main__':
    sys.exit(main())
