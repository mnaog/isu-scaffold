#!/usr/bin/env python3
"""Raise the generic connection and file limits as one commit (`make tare`).

Run once after the first baseline. Every change only lifts a default limit that is
independent of the problem; durability (fsync, binlog) and anything that changes what the
application does are left out. Left at their defaults these limits make a busy benchmark FAIL:
nginx closing keep-alive connections after its default request count, the application's default
open-file limit, the default listen backlog and worker connections.

- nginx.conf: worker_rlimit_nofile, worker_connections, client keepalive_requests, and the
  main error_log at warn (connection shortages are logged only at warn)
- site files: backlog on one listen per address:port, and an upstream with keepalive for
  each literal `proxy_pass http://host:port` (Host and inherited headers are preserved)
- the Rust unit (the synced rust-service item): LimitNOFILE
- /etc/sysctl.d/99-tare.conf: accept queues, SYN backlog, ephemeral ports, TIME_WAIT reuse
- MySQL: max_connections, and innodb_buffer_pool_size from the DB node's memory
- deploy commands: daemon-reload, loading 99-tare.conf, and a MySQL restart to load them, each
  limited by "when" to deploys that change the unit, the sysctl file or the MySQL settings

The edited files are committed together. Rerunning on an applied tree changes nothing.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMITS = {
    'worker_rlimit_nofile': '131072',
    'worker_connections': '32768',
    'keepalive_requests': '10000',
    'backlog': '4096',
    'upstream_keepalive': '128',
    'upstream_keepalive_requests': '100000',
    'LimitNOFILE': '1048576',
    'max_connections': '1000',
}
SYSCTL = """# make tare: generic limits, independent of the application
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.core.netdev_max_backlog = 16384
net.ipv4.ip_local_port_range = 10000 65535
net.ipv4.tcp_tw_reuse = 1
"""
PROXY_TARGET = re.compile(r'^http://(?P<host>[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\]):(?P<port>\d+)(?P<uri>/\S*)?$')


class Statement:
    def __init__(self, path, words, start, end):
        self.path, self.words, self.start, self.end = path, words, start, end


class Block:
    def __init__(self, path, header_start, open_index):
        self.path, self.header_start, self.open, self.close = path, header_start, open_index, None

    def contains(self, position):
        return self.open <= position and (self.close is None or position < self.close)


def scan(text):
    """Statements and blocks of an nginx configuration, with their positions in the text.

    A block's path is its headers from the file's top level, such as
    ('server', 'location /api/'); a statement carries the path of its block.
    """
    statements, blocks, stack = [], [], []
    words, word, start, quote = [], None, None, None
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            word += char
            if char == '\\' and index + 1 < len(text):
                index += 1
                word += text[index]
            elif char == quote:
                quote = None
        elif char == '#' and word is None:
            newline = text.find('\n', index)
            index = len(text) if newline < 0 else newline
            continue
        elif char.isspace() or char in '{};':
            if word is not None:
                words.append(word)
                word = None
            if char in '{;}':
                position = start if start is not None else index
                path = stack[-1].path if stack else ()
                if char == '{':
                    block = Block(path + (' '.join(words),), position, index + 1)
                    blocks.append(block)
                    stack.append(block)
                elif char == ';':
                    statements.append(Statement(path, words, position, index + 1))
                elif stack:
                    stack.pop().close = index
                words, start = [], None
        else:
            if word is None:
                word = ''
                if not words:
                    start = index
            if char in '\'"':
                quote = char
            word += char
        index += 1
    return statements, blocks


def block_path(statement_path):
    return tuple(part.split()[0] if part else part for part in statement_path)


def indent_at(text, position):
    line_start = text.rfind('\n', 0, position) + 1
    return re.match(r'[ \t]*', text[line_start:]).group(0)


def set_directive(text, context, name, value, after=()):
    """Replace or insert `name value;` in the block whose kind path is `context`."""
    statements, blocks = scan(text)
    for statement in statements:
        if block_path(statement.path) == context and statement.words and statement.words[0] == name:
            if statement.words[1:] == value.split():
                return text
            return text[:statement.start] + f'{name} {value};' + text[statement.end:]
    if not context:
        anchor = next((s for name_after in after for s in statements
                       if not s.path and s.words and s.words[0] == name_after), None)
        if anchor:
            return text[:anchor.end] + f'\n{name} {value};' + text[anchor.end:]
        return f'{name} {value};\n' + text
    block = next((b for b in blocks if block_path(b.path) == context), None)
    if block is None:
        return text
    first_child = next((s for s in statements if s.path == block.path and block.contains(s.start)), None)
    child_indent = indent_at(text, first_child.start) if first_child else indent_at(text, block.header_start) + '    '
    return text[:block.open] + f'\n{child_indent}{name} {value};' + text[block.open:]


def tare_nginx_main(text):
    text = set_directive(text, (), 'worker_rlimit_nofile', LIMITS['worker_rlimit_nofile'],
                         after=('worker_processes', 'pid', 'user'))
    text = set_directive(text, ('events',), 'worker_connections', LIMITS['worker_connections'])
    text = set_directive(text, ('http',), 'keepalive_requests', LIMITS['keepalive_requests'])
    statements, _ = scan(text)
    error_log = next((s for s in statements if not s.path and s.words and s.words[0] == 'error_log'), None)
    if error_log is None:
        text = set_directive(text, (), 'error_log', '/var/log/nginx/error.log warn',
                             after=('worker_processes', 'pid', 'user'))
    elif len(error_log.words) == 2 or error_log.words[2] in ('error', 'crit', 'alert', 'emerg'):
        text = text[:error_log.start] + f'error_log {error_log.words[1]} warn;' + text[error_log.end:]
    return text


def listen_key(address):
    if address.startswith('unix:'):
        return address
    if address.isdigit():
        return '*:' + address
    if address.startswith('0.0.0.0:'):
        return '*:' + address.split(':', 1)[1]
    if ':' not in address.rsplit(']', 1)[-1]:
        return address + ':80'
    return address


def add_backlogs(texts):
    """Set backlog on one listen directive per address:port across all files."""
    listens = []
    for name, text in texts.items():
        statements, _ = scan(text)
        for statement in statements:
            if statement.words and statement.words[0] == 'listen' and len(statement.words) > 1:
                listens.append((name, statement))
    groups = {}
    for name, statement in listens:
        groups.setdefault(listen_key(statement.words[1]), []).append((name, statement))
    edits = {}
    for group in groups.values():
        if any(any(word.startswith('backlog=') for word in statement.words) for _, statement in group):
            continue
        chosen = next(((n, s) for n, s in group if 'default_server' in s.words or 'default' in s.words), group[0])
        edits.setdefault(chosen[0], []).append(chosen[1])
    for name, statements in edits.items():
        text = texts[name]
        for statement in sorted(statements, key=lambda s: s.start, reverse=True):
            words = statement.words + [f"backlog={LIMITS['backlog']}"]
            text = text[:statement.start] + ' '.join(words) + ';' + text[statement.end:]
        texts[name] = text
    return texts


def upstream_name(host, port):
    return 'tare_' + re.sub(r'[^A-Za-z0-9]+', '_', host).strip('_') + f'_{port}'


def add_upstream_keepalive(texts, defined):
    """Route literal proxy_pass targets through an upstream with keepalive.

    proxy_set_header is inherited from the nearest enclosing level only while a level defines
    none, so a location that gains `Connection ""` also gets the headers it inherited. When no
    Host header applies, the old Host (the proxy_pass host:port) is set explicitly, because
    $proxy_host would become the upstream name.
    """
    for name in list(texts):
        text = texts[name]
        statements, blocks = scan(text)
        targets = []
        for statement in statements:
            if not statement.words or statement.words[0] != 'proxy_pass' or len(statement.words) != 2:
                continue
            match = PROXY_TARGET.match(statement.words[1])
            if match and '$' not in statement.words[1]:
                targets.append((statement, match))
        if not targets:
            continue

        def directives(block, directive):
            return [s for s in statements if s.words and s.words[0] == directive and s.path == block.path
                    and block.contains(s.start)]

        new_upstreams = []
        for statement, match in sorted(targets, key=lambda item: item[0].start, reverse=True):
            host, port, uri = match['host'], match['port'], match['uri'] or ''
            upstream = upstream_name(host, port)
            if upstream not in defined:
                defined.add(upstream)
                new_upstreams.append((upstream, host, port))
            ancestors = sorted((b for b in blocks if b.contains(statement.start)), key=lambda b: len(b.path), reverse=True)
            if not ancestors:
                continue
            location = ancestors[0]
            own = directives(location, 'proxy_set_header')
            effective = own or next((found for found in (directives(b, 'proxy_set_header') for b in ancestors[1:]) if found), [])
            inserted = []
            if not any(directives(b, 'proxy_http_version') for b in ancestors):
                inserted.append('proxy_http_version 1.1;')
            if not own:
                inserted += [' '.join(s.words) + ';' for s in effective]
            if not any(len(s.words) > 1 and s.words[1].lower() == 'host' for s in effective):
                inserted.append(f'proxy_set_header Host {host}:{port};')
            if not any(len(s.words) > 1 and s.words[1].lower() == 'connection' for s in effective):
                inserted.append('proxy_set_header Connection "";')
            indent = indent_at(text, statement.start)
            replacement = ''.join(f'{line}\n{indent}' for line in inserted) + f'proxy_pass http://{upstream}{uri};'
            text = text[:statement.start] + replacement + text[statement.end:]
        header = ''.join(
            f"# make tare: reuse connections to {host}:{port}\nupstream {upstream} {{\n"
            f"  server {host}:{port};\n  keepalive {LIMITS['upstream_keepalive']};\n"
            f"  keepalive_requests {LIMITS['upstream_keepalive_requests']};\n}}\n\n"
            for upstream, host, port in reversed(new_upstreams))
        texts[name] = header + text
    return texts


def tare_unit(text):
    lines = text.splitlines(keepends=True)
    setting = f"LimitNOFILE={LIMITS['LimitNOFILE']}\n"
    service = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('['):
            if service is not None:
                break
            service = index if stripped == '[Service]' else None
        elif service is not None and stripped.startswith('LimitNOFILE='):
            lines[index] = setting
            return ''.join(lines)
    if service is None:
        return text + ('' if text.endswith('\n') else '\n') + '\n[Service]\n' + setting
    lines.insert(service + 1, setting)
    return ''.join(lines)


def buffer_pool_mb(root, sync, mysql_items):
    """40% of the DB node's memory beside the application, 70% when it runs MySQL alone."""
    source = (mysql_items[0].get('source_node') if mysql_items else None) or sync.get('source_node')
    report_path = root / '.local/inspection' / f'{source}.json'
    try:
        report = json.loads(report_path.read_text())
    except (OSError, ValueError):
        return None, f'no inspection report for {source}; innodb_buffer_pool_size left unchanged'
    memory = int(report.get('memory_mb') or 0)
    if memory <= 0:
        return None, f'{source} reported no memory size; innodb_buffer_pool_size left unchanged'
    alone = not any(token in ' '.join(report.get('running_services', [])) for token in ('nginx', 'isu'))
    size = max(128, int(memory * (0.7 if alone else 0.4)) // 128 * 128)
    return size, f'innodb_buffer_pool_size {size}M = {"70" if alone else "40"}% of {source} {memory} MiB'


def ensure_command(commands, entry, first=False, before=None, replaces=()):
    if entry in commands:
        return commands
    # The same command from an earlier tare (without "when", or an older form) is updated in place.
    for index, command in enumerate(commands):
        if isinstance(command, dict) and command.get('node_group') == entry['node_group'] and \
                command.get('command') in (entry['command'], *replaces):
            return commands[:index] + [entry] + commands[index + 1:]
    if first:
        return [entry] + commands
    if before is not None:
        for index, command in enumerate(commands):
            text = command if isinstance(command, str) else command.get('command', '')
            if before(text):
                return commands[:index] + [entry] + commands[index:]
    return commands + [entry]


def git(root, *args, check=True):
    return subprocess.run(['git', '-C', str(root), *args], check=check, capture_output=True, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--no-commit', action='store_true', help='edit the files without committing them')
    args = parser.parse_args()
    root = args.root.resolve()
    sync_path = root / 'config/sync.json'
    sync = json.loads(sync_path.read_text())
    items = sync.get('items', [])
    notes, changed = [], {}

    def local(item):
        return root / item['local']

    nginx_conf = next((item for item in items if item.get('remote') == '/etc/nginx/nginx.conf'), None)
    site_items = [item for item in items if item.get('node_group') == 'role_nginx' and item.get('type') == 'file'
                  and item is not nginx_conf and item['remote'].startswith(('/etc/nginx/', '/'))
                  and item['name'].startswith(('nginx-site-', 'nginx-confd-', 'nginx-file-'))]
    nginx_files = {}
    if nginx_conf and local(nginx_conf).is_file():
        nginx_files[nginx_conf['local']] = tare_nginx_main(local(nginx_conf).read_text())
    else:
        notes.append('nginx.conf is not a sync item; nginx limits were not raised')
    for item in site_items:
        if local(item).is_file():
            nginx_files[item['local']] = local(item).read_text()
    if nginx_files:
        nginx_files = add_backlogs(nginx_files)
        defined = set()
        for text in nginx_files.values():
            defined |= {b.path[-1].split()[1] for b in scan(text)[1] if b.path[-1].startswith('upstream ')}
        site_texts = {name: text for name, text in nginx_files.items() if not nginx_conf or name != nginx_conf['local']}
        nginx_files.update(add_upstream_keepalive(site_texts, defined))
        for name, text in nginx_files.items():
            if text != (root / name).read_text():
                changed[name] = text

    unit = next((item for item in items if item.get('name') == 'rust-service'), None)
    if unit and local(unit).is_file():
        text = tare_unit(local(unit).read_text())
        if text != local(unit).read_text():
            changed[unit['local']] = text
    else:
        notes.append('the Rust unit is not a sync item; LimitNOFILE was not raised')

    sysctl_local = 'config/sysctl/99-tare.conf'
    if not (root / sysctl_local).is_file() or (root / sysctl_local).read_text() != SYSCTL:
        changed[sysctl_local] = SYSCTL
    if not any(item.get('remote') == '/etc/sysctl.d/99-tare.conf' for item in items):
        items.append({'name': 'tare-sysctl', 'type': 'file', 'node_group': 'application', 'local': sysctl_local,
                      'remote': '/etc/sysctl.d/99-tare.conf', 'owner': 'root', 'owner_group': 'root'})

    mysql_items = [item for item in items if item.get('node_group') == 'role_mysql']
    # my.cnf reads conf.d before mysql.conf.d, so a file in mysql.conf.d wins over both.
    mysql_dir = next((item for remote in ('/etc/mysql/mysql.conf.d', '/etc/mysql/conf.d') for item in mysql_items
                      if item['type'] == 'directory' and item['remote'] == remote), None)
    if mysql_dir:
        size, note = buffer_pool_mb(root, sync, mysql_items)
        notes.append(note)
        lines = ['# make tare: limits from the DB node, independent of the application', '[mysqld]',
                 f"max_connections = {LIMITS['max_connections']}"]
        if size:
            lines.append(f'innodb_buffer_pool_size = {size}M')
        mysql_local = mysql_dir['local'].rstrip('/') + '/zz-tare.cnf'
        text = '\n'.join(lines) + '\n'
        if not (root / mysql_local).is_file() or (root / mysql_local).read_text() != text:
            changed[mysql_local] = text
    elif mysql_items:
        notes.append('MySQL configuration is not synced as a directory; add max_connections by hand')

    # "when" keeps each command to deploys that change its files (scripts/deploy-plan.py):
    # a Rust-only change must not restart MySQL and empty its buffer pool before every run.
    reload = {'node_group': 'application', 'command': 'sudo systemctl daemon-reload', 'when': ['rust-service']}
    # Only tare's own file: `sysctl --system` also reloads the OS defaults, whose keys the instance
    # may not have, and prints "Invalid argument" after every deploy.
    sysctl = {'node_group': 'application', 'command': 'sudo sysctl -q -p /etc/sysctl.d/99-tare.conf', 'when': ['tare-sysctl']}
    restart_mysql = {'node_group': 'role_mysql', 'command': 'sudo systemctl restart mysql', 'when': ['mysql-*']}
    for key in ('post_deploy_commands', 'rollback_commands'):
        commands = sync.get(key, [])
        commands = ensure_command(commands, sysctl, first=True, replaces=('sudo sysctl --system >/dev/null',))
        commands = ensure_command(commands, reload, first=True)
        if mysql_dir:
            commands = ensure_command(commands, restart_mysql,
                                      before=lambda text: 'is-active' in text and 'mysql' in text)
        sync[key] = commands
    sync_text = json.dumps(sync, ensure_ascii=False, indent=2) + '\n'
    if sync_text != sync_path.read_text():
        changed['config/sync.json'] = sync_text

    if not changed:
        print('tare is already applied; nothing to change')
        return 0
    dirty = git(root, 'status', '--porcelain', '--', *changed).stdout.strip()
    if dirty and not args.no_commit:
        print(f'refusing to mix with uncommitted changes:\n{dirty}', file=sys.stderr)
        return 1
    for name, text in changed.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f'updated {name}')
    for note in notes:
        print(f'note: {note}')
    if args.no_commit:
        return 0
    git(root, 'add', '--', *changed)
    message = ('Raise connection and file limits (make tare)\n\n'
               'Generic limits only; see scripts/tare.py. Measure it as one change with isuscope run.\n\n'
               + ''.join(f'- {name}\n' for name in changed) + ''.join(f'- note: {note}\n' for note in notes))
    git(root, 'commit', '-q', '-m', message)
    print(git(root, 'log', '-1', '--format=%h %s').stdout.strip())
    print('next: make deploy, then isuscope run --hypothesis "..." to measure it')
    return 0


if __name__ == '__main__':
    sys.exit(main())
