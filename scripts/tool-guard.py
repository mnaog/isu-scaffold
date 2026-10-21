#!/usr/bin/env python3
"""PreToolUse hook that stops the practice rule violations seen so far.

Claude Code (.claude/settings.json) and Codex (.codex/hooks.json) pass the tool call as
JSON on stdin. A denied call prints a PreToolUse decision with the reason; anything
else prints nothing and lets the call through. The hook only sees the command it is
given, so scripts/ssh-node.sh refuses the benchmark node by itself as well.

Stopped calls:
- a direct SSH, scp, rsync, sftp or ssh-node.sh session to the benchmark node
  (benchmarks run through isuscope and .isuscope/benchmark.sh, which the hook does not see)
- setting ISUCON_ALLOW_BENCHMARK_NODE, which only the adapter and bootstrap may set
- reading or copying the benchmarker: `benchmarker_paths`, and `<keyword>.../bench`
  for each contest keyword (for example a clone of the contest repository)
- fetching or cloning the current contest's code from GitHub, or fetching a page about it
  outside `allowed_urls`, and web searches about the current contest

While a main session is declared (/main, scripts/main-session.py), every other session is also
refused what only main does: deploy, rollback, import, kickoff, the benchmark (not the adapter's
--check and --probe), isuscope records, deciding worktree proposals, starting or stopping lanes, and the operation lock.
Calls without a session id, and
every call while no main is declared, are not checked for this.

Contest keywords and paths come from config/tool-guard.json. Empty lists turn those rules
off; the benchmark node rule always applies. Internal errors let the call through, so a
broken guard never stops all work.
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOW_VARIABLE = 'ISUCON_ALLOW_BENCHMARK_NODE'
SSH_PROGRAMS = {'ssh', 'mosh', 'autossh'}
COPY_PROGRAMS = {'scp', 'rsync', 'sftp'}
# ssh options that take a value; the host is the first argument after the options.
SSH_VALUE_OPTIONS = set('BbcDEeFIiJLlmOoPpQRSWw')
CODE_HOSTS = {'github.com', 'www.github.com', 'raw.githubusercontent.com', 'gist.github.com',
              'gist.githubusercontent.com', 'codeload.github.com', 'api.github.com'}
WRAPPERS = {'sudo', 'env', 'nice', 'nohup', 'time', 'exec', 'command', 'timeout', 'gtimeout',
            'xargs', 'stdbuf', 'ionice'}
SHELLS = {'bash', 'sh', 'zsh', 'dash'}
# Fields that name something to read or fetch. Text being written (content, new_string) is not checked.
PATH_KEYS = {'file_path', 'path', 'paths', 'notebook_path', 'filePath', 'pattern', 'glob', 'directory', 'dir',
             'url', 'uri'}
QUERY_KEYS = {'query', 'q', 'search_query'}
URL = re.compile(r'(?:https?://|git@)[^\s\'"<>()`]+')
OPERATORS = {';', '&&', '||', '|', '&', '(', ')', '\n', ';;', '|&'}
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
# Programs whose heredoc or arguments are text being written, not something read or run.
DATA_WRITERS = {'cat', 'tee', 'echo', 'printf'}
WRITE_HINT = '説明や設定に書くだけなら、Write・Editツールで書く。'
LANE_HINT = 'laneはremote node・deploy・ベンチ・操作lockに触れない。必要な操作は最後の回答でmainのセッションへ頼む。'
# Scripts and make targets that reach remote nodes, the benchmark or the operation lock.
LANE_SCRIPTS = {'deploy.sh', 'rollback.sh', 'import.sh', 'kickoff.sh', 'kickoff-apply.sh', 'bootstrap.sh',
                'bootstrap-ssh.sh', 'discover.sh', 'configure-draft.sh', 'configure-apply.sh',
                'inspect-environment.sh', 'bench-check.sh', 'quick-import-code.sh', 'run-ansible-playbook.sh',
                'status.sh', 'switch-language-service.sh', 'sync-check.sh', 'benchmark.sh',
                'benchmark-prepare.sh', 'ansible-install.sh', 'tare.py', 'kickoff-build.py',
                'lane.py', 'review-draft.py', 'check-deployed-commit.sh', 'first-bench.sh',
                'benchmark-markers.py', 'deploy-plan.py', 'final-verification.py'}
LANE_MAKE_TARGETS = {'deploy', 'rollback', 'status', 'final-check', 'final-reboot', 'final-db-late', 'kickoff', 'kickoff-apply', 'discover', 'bootstrap',
                     'bench-check', 'first-bench', 'tare'}
# Read-only isuscope commands; everything else records, benchmarks or reaches nodes.
LANE_ISUSCOPE = {'list', 'brief', 'query', 'sql', 'series', 'help', '--help', '-h', '--version', 'version'}
# `isuscope change` subcommands that only read; `decide` records.
READ_CHANGE = {'list', 'show', 'help', '--help', '-h'}
LANE_PROGRAMS = {'ansible', 'ansible-playbook', 'aws', 'gh', 'claude', 'codex', 'opencode'}
MAIN_HINT = ('この操作はmainのセッション（/mainで宣言したセッション）だけが行う。'
             'worktreeの変更はcommitして`python3 scripts/proposal.py submit --hypothesis ... --expect ...`'
             'で投稿すると、mainがベンチを起動したときに渡る。mainを引き継ぐならrepoのrootで/mainを打つ。')
# What only main does while a main is declared: changes to remote nodes, the benchmark and its records.
MAIN_SCRIPTS = {'deploy.sh', 'rollback.sh', 'import.sh', 'kickoff.sh', 'kickoff-apply.sh', 'bootstrap.sh',
                'bootstrap-ssh.sh', 'configure-apply.sh', 'bench-check.sh', 'quick-import-code.sh',
                'run-ansible-playbook.sh', 'switch-language-service.sh', 'benchmark.sh', 'benchmark-prepare.sh',
                'benchmark-stop.sh', 'ansible-install.sh', 'tare.py', 'first-bench.sh',
                'final-verification.py'}
MAIN_MAKE_TARGETS = {'deploy', 'rollback', 'final-reboot', 'final-db-late', 'kickoff', 'kickoff-apply', 'discover', 'bootstrap', 'bench-check',
                     'first-bench', 'tare'}
LOCAL_URL = re.compile(r'^(?:https?://)?(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:/|$)')


class Denied(Exception):
    pass


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def env_file(path):
    values = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        try:
            parts = shlex.split(value)
        except ValueError:
            parts = [value]
        values[key.strip()] = parts[0] if parts else ''
    return values


def benchmark_ids(root):
    """Names and addresses of the benchmark node from the inventory, discovery and benchmark.env."""
    ids = set()
    inventory = load_json(root / '.local/ansible-inventory.json', {})
    children = inventory.get('all', {}).get('children', {}) if isinstance(inventory, dict) else {}
    for name, entry in children.get('benchmark', {}).get('hosts', {}).items():
        ids.add(name)
        if isinstance(entry, dict) and entry.get('ansible_host'):
            ids.add(str(entry['ansible_host']))
    nodes = load_json(root / '.local/discovered-nodes.json', [])
    for node in nodes if isinstance(nodes, list) else []:
        if isinstance(node, dict) and node.get('group') == 'benchmark':
            for key in ('name', 'display_name', 'host', 'public_ip', 'private_ip'):
                if node.get(key):
                    ids.add(str(node[key]))
    benchmark = env_file(root / 'config/benchmark.env')
    if benchmark.get('BENCHMARK_TRANSPORT') == 'ssh' and benchmark.get('BENCHMARK_NODE'):
        ids.add(benchmark['BENCHMARK_NODE'])
    return {value for value in ids if value}


def strip_data_heredocs(text, everything=False):
    """Drops heredoc bodies fed to cat, tee or git (documents, commit messages), or every body."""
    lines = text.split('\n')
    kept = []
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        match = HEREDOC.search(line)
        if match:
            segment = re.split(r'&&|\|\||[;|&]', line[:match.start()])[-1].split()
            consumer = os.path.basename(segment[0]) if segment else ''
            end = index + 1
            while end < len(lines) and lines[end].strip() != match.group(2):
                end += 1
            if not everything and consumer not in DATA_WRITERS | {'git'}:
                kept.extend(lines[index + 1:end])
            if end < len(lines):
                kept.append(lines[end])
            index = end
        index += 1
    return '\n'.join(kept)


def host_of(target):
    host = target.rsplit('@', 1)[-1]
    return host.strip('[]')


class Guard:
    def __init__(self, root):
        config = load_json(root / 'config/tool-guard.json', {})
        self.keywords = [k.lower() for k in config.get('contest_keywords', []) if k]
        self.allowed_urls = [u for u in config.get('allowed_urls', []) if u]
        self.bench_ids = benchmark_ids(root)
        self.lane = os.environ.get('ISUCON_LANE')
        self.root = root
        self.not_main = False  # set per call: a main is declared and this session is not it
        self.drop_heredocs = False  # bench-hook.py reads only the command lines, not heredoc bodies
        patterns = [re.escape(p.rstrip('/')) + r'(?![\w.-])' for p in config.get('benchmarker_paths', []) if p]
        patterns += [r'(?<![\w.-])' + re.escape(k) + r'[\w.-]*/bench(?![\w.-])' for k in self.keywords]
        self.bench_paths = re.compile('|'.join(patterns), re.I) if patterns else None

    # --- rules -------------------------------------------------------------------------
    def check_text_paths(self, text):
        if self.bench_paths and (match := self.bench_paths.search(text)):
            raise Denied(f'ベンチマーカーの中身（{match.group(0)}）は読まない・写さない（練習の規則）。{WRITE_HINT}')

    def check_url(self, url, fetching_page):
        lowered = url.lower()
        if not self.keywords or not any(k in lowered for k in self.keywords):
            return
        if any(url.startswith(prefix) for prefix in self.allowed_urls):
            return
        host = re.sub(r'^(?:https?://|git@)', '', lowered).split('/', 1)[0].split(':', 1)[0]
        if fetching_page or host in CODE_HOSTS:
            raise Denied(f'現在練習中の開催回のコードや攻略情報は取得しない（{url}）。'
                         f'公式の配布資料なら config/tool-guard.json の allowed_urls に加える。{WRITE_HINT}')

    def check_bench_host(self, target):
        if host_of(target) in self.bench_ids:
            raise Denied(f'ベンチnode（{host_of(target)}）へ直接入らない。ベンチは isuscope run / survey-run'
                         '（.isuscope/benchmark.sh）からだけ動かし、ベンチnodeの観測もしない。')

    # --- shell commands ----------------------------------------------------------------
    def check_shell(self, text, depth=0):
        if depth > 4 or not text:
            return
        text = strip_data_heredocs(text, self.drop_heredocs)
        if re.search(r'(?<![\w])' + ALLOW_VARIABLE + r'\s*=', text):
            raise Denied(f'{ALLOW_VARIABLE} はベンチadapterとkickoffの検査だけが設定する。')
        try:
            # A newline outside quotes ends a command; inside quotes it stays in the word.
            lexer = shlex.shlex(text, posix=True, punctuation_chars=';&|()\n')
            lexer.whitespace = ' \t\r'
            lexer.whitespace_split = True
            lexer.commenters = ''
            tokens = list(lexer)
        except ValueError:
            self.check_text_paths(text)
            for url in URL.findall(text):
                self.check_url(url, fetching_page=False)
            self.check_shell_lines(text)
            return
        command = []
        for token in tokens + [';']:
            if token in OPERATORS or set(token) <= set(';&|()\n'):
                if command:
                    self.check_simple(command, depth)
                command = []
            else:
                command.append(token)

    def check_shell_lines(self, text):
        """Fallback for commands shlex cannot split (heredocs with unbalanced quotes)."""
        ids = '|'.join(re.escape(i) for i in sorted(self.bench_ids, key=len, reverse=True))
        if not ids:
            return
        patterns = [
            r'ssh-node\.sh["\']?\s+["\']?(?:' + ids + r')(?![\w.-])',
            r'(?<![\w.-])(?:ssh|mosh|autossh)\s+(?:-\S+\s+(?:\S+\s+)?)*(?:[\w.-]+@)?(?:' + ids + r')(?![\w.-])',
            r'(?<![\w.-])(?:scp|rsync|sftp)\s[^\n;|&]*?(?:[\w.-]+@)?(?:' + ids + r'):',
        ]
        for line in text.splitlines():
            for pattern in patterns:
                if re.search(pattern, line):
                    raise Denied('ベンチnodeへ直接入らない。ベンチは isuscope run / survey-run'
                                 '（.isuscope/benchmark.sh）からだけ動かし、ベンチnodeの観測もしない。')

    def check_arguments(self, words):
        for word in words:
            self.check_text_paths(word)
            for url in URL.findall(word):
                self.check_url(url, fetching_page=False)

    def check_simple(self, words, depth):
        words = list(words)
        while words:
            head = os.path.basename(words[0])
            if re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', words[0]):
                self.check_arguments([words[0].split('=', 1)[1]])
                words = words[1:]
            elif head in WRAPPERS:
                words = words[1:]
                while words and (words[0].startswith('-') or re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', words[0])
                                 or (head in {'timeout', 'gtimeout'} and re.match(r'^\d', words[0]))
                                 or (head in {'sudo', 'nice', 'ionice'} and words[0].isdigit())):
                    option = words.pop(0)
                    if head == 'sudo' and option in {'-u', '-g', '-C', '-h', '-p'} and words:
                        words.pop(0)
                    if head == 'nice' and option == '-n' and words:
                        words.pop(0)
            elif head == 'isuscope' and len(words) > 1 and words[1] == 'lock':
                if self.lane:
                    raise Denied(LANE_HINT)
                if self.not_main:
                    raise Denied(MAIN_HINT)
                words = words[words.index('--') + 1:] if '--' in words else []
            elif head in SHELLS and len(words) > 2 and re.match(r'^-[a-z]*c[a-z]*$', words[1]):
                self.check_shell(words[2], depth + 1)
                return
            else:
                break
        if not words:
            return
        program = os.path.basename(words[0])
        args = words[1:]
        writing_text = program in DATA_WRITERS or (program == 'git' and args[:1] in (['commit'], ['tag'], ['notes']))
        if not writing_text or program == 'cat':
            # cat with arguments reads files; only its heredoc body was dropped as text.
            self.check_arguments(words)
        if self.lane:
            self.check_lane(program, args)
        if self.not_main:
            self.check_main_only(program, args)
        self.seen(program, args)
        if program == 'ssh-node.sh':
            if args:
                self.check_bench_host(args[0])
                self.check_shell(' '.join(args[1:]), depth + 1)
        elif program in SSH_PROGRAMS:
            index = 0
            while index < len(args) and args[index].startswith('-'):
                option = args[index]
                index += 1
                if len(option) == 2 and option[1] in SSH_VALUE_OPTIONS:
                    index += 1
            if index < len(args):
                self.check_bench_host(args[index])
                self.check_shell(' '.join(args[index + 1:]), depth + 1)
        elif program in COPY_PROGRAMS:
            for arg in args:
                match = re.match(r'^(?:[\w.-]+@)?([\w.-]+):', arg)
                if match:
                    self.check_bench_host(match.group(1))
            if program == 'sftp':
                hosts = [a for a in args if not a.startswith('-')]
                if hosts:
                    self.check_bench_host(hosts[0])
        elif program == 'ansible':
            for arg in args:
                if arg == 'benchmark' or host_of(arg) in self.bench_ids:
                    raise Denied('ベンチnodeへ直接入らない。ベンチは isuscope run / survey-run'
                                 '（.isuscope/benchmark.sh）からだけ動かし、ベンチnodeの観測もしない。')
        elif program == 'gh' and self.keywords:
            lowered = ' '.join(args).lower()
            if any(k in lowered for k in self.keywords):
                raise Denied('現在練習中の開催回のコードや攻略情報をGitHubから取得・検索しない。')

    def check_lane(self, program, args):
        """An AI lane works on its own branch; remote, benchmark and lock work stays with main."""
        script = program
        if program in SHELLS | {'python', 'python3'}:
            script = os.path.basename(next((a for a in args if not a.startswith('-')), ''))
        if (program in SSH_PROGRAMS | COPY_PROGRAMS | LANE_PROGRAMS or program == 'ssh-node.sh'
                or script in LANE_SCRIPTS or program in LANE_SCRIPTS
                or any('operation.lock' in a for a in args)):
            raise Denied(LANE_HINT)
        if program == 'make' and any(a in LANE_MAKE_TARGETS for a in args if not a.startswith('-')):
            raise Denied(LANE_HINT)
        if program == 'isuscope' and (not args or args[0] not in LANE_ISUSCOPE):
            raise Denied(LANE_HINT)
        if program == 'git' and 'push' in args:
            raise Denied(LANE_HINT)
        if program in {'curl', 'wget'} and not all(LOCAL_URL.match(a) for a in args if not a.startswith('-')
                                                  and ('.' in a or ':' in a or '/' in a)):
            raise Denied(LANE_HINT)

    def check_main_only(self, program, args):
        """While a main is declared, other sessions leave remote changes and the benchmark to it."""
        script, script_args = program, args
        operands = [a for a in args if not a.startswith('-')]
        if program in SHELLS | {'python', 'python3'}:
            script = os.path.basename(operands[0]) if operands else ''
            script_args = args[args.index(operands[0]) + 1:] if operands else []
            operands = operands[1:]
        # The adapter's --check and --probe never start a benchmark: the portal session (/portal)
        # builds the connection with them while main runs kickoff.
        checking = ((script == 'benchmark.sh' and script_args[:1] in (['--check'], ['--probe']))
                    or (script == 'final-verification.py' and script_args[:1] == ['check']))
        if ((script in MAIN_SCRIPTS and not checking) or program in {'ansible', 'ansible-playbook'}
                or any('operation.lock' in a for a in args)):
            raise Denied(MAIN_HINT)
        if script == 'lane.py' and operands[:1] and operands[0] in {'start', 'kickoff', 'stop'}:
            raise Denied(MAIN_HINT)
        # Measuring and deciding a proposal is main's; improve and people submit and read.
        if script == 'proposal.py' and operands[:1] and operands[0] in {'decide', 'pending', 'sync', 'hold', 'unhold',
                                                                         'requeue', 'freeze', 'adopt'}:
            raise Denied(MAIN_HINT)
        if program == 'make' and any(a in MAIN_MAKE_TARGETS for a in operands):
            raise Denied(MAIN_HINT)
        # Reading runs (states, route suggestions included) helps a worktree; recording them is main's.
        reading_changes = args[:1] == ['change'] and len(args) > 1 and args[1] in READ_CHANGE
        if (program == 'isuscope' and args and args[0] not in LANE_ISUSCOPE | {'states', 'routes'}
                and not reading_changes):
            raise Denied(MAIN_HINT)

    def seen(self, program, args):
        """Every simple command, after wrappers are removed. bench-hook.py watches for benchmarks here."""

    # --- tool calls --------------------------------------------------------------------
    def check(self, payload):
        tool = str(payload.get('tool_name') or '')
        tool_input = payload.get('tool_input')
        if not isinstance(tool_input, dict):
            tool_input = {'value': tool_input}
        command = command_of(tool_input)
        self.not_main = not_main(self.root, payload.get('session_id'))
        if isinstance(command, str):
            if self.lane and 'operation.lock' in command:
                raise Denied(LANE_HINT)
            if self.not_main and 'operation.lock' in command:
                raise Denied(MAIN_HINT)
            self.check_shell(command)
        searching = 'search' in tool.lower()
        for key, value in tool_input.items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                if not isinstance(item, str):
                    continue
                if key in PATH_KEYS:
                    self.check_text_paths(item)
                    if item.startswith(('http://', 'https://', 'git@')):
                        self.check_url(item, fetching_page=True)
                if searching and key in QUERY_KEYS and self.keywords:
                    lowered = item.lower()
                    if any(k in lowered for k in self.keywords):
                        raise Denied(f'現在練習中の開催回の攻略法は検索しない（{item}）。')


def not_main(root, session):
    """True when a main session is declared (in the root repository) and this call's session is another.

    The session is the hook payload's id, or the agent's own variables (Codex's thread id, which
    `main-session.py claim --agent codex` records, need not be the id its hooks are given).
    """
    ids = {str(session or '')} | {os.environ.get(name, '') for name in
                                  ('CLAUDE_CODE_SESSION_ID', 'CODEX_SESSION_ID', 'CODEX_THREAD_ID')}
    ids.discard('')
    if not ids:
        return False
    try:
        common = subprocess.run(['git', '-C', str(root), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                                capture_output=True, text=True, timeout=5).stdout.strip()
        declared = load_json(Path(common).parent / '.local/main-session.json' if common
                             else Path(root) / '.local/main-session.json', {})
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(isinstance(declared, dict) and declared.get('session')) and declared['session'] not in ids


def command_of(tool_input):
    """The shell command of a Bash (Claude Code) or shell (Codex) call, as one string."""
    command = tool_input.get('command', tool_input.get('cmd'))
    if isinstance(command, list):
        words = [str(word) for word in command]
        if len(words) > 2 and os.path.basename(words[0]) in SHELLS and re.match(r'^-[a-z]*c[a-z]*$', words[1]):
            return words[2]
        return shlex.join(words)
    return command


def main():
    try:
        payload = json.load(sys.stdin)
        root = Path(os.environ.get('TOOL_GUARD_ROOT') or ROOT)
        Guard(root).check(payload if isinstance(payload, dict) else {})
    except Denied as denied:
        print(json.dumps({'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': str(denied),
        }}, ensure_ascii=False))
    except Exception as error:  # a broken guard must not stop every tool call
        print(f'tool-guard: skipped ({error})', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
