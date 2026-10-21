#!/usr/bin/env python3
"""Which session is main: the one that deploys, benchmarks and decides, declared with /main.

  main-session.py claim [--agent claude|codex] [--session ID]   this session becomes main
  main-session.py show                                         print the declared main
  main-session.py release [--force]                            main steps down
  main-session.py badge [--session ID]                         JSON the Claude Code mod draws

The declaration is .local/main-session.json in the root repository, shared by every worktree.
Claude Code declares it with /main (.claude/commands/main.md), Codex with `claim --agent codex`.
Claiming again from another session takes main over and says whose it was. Main runs in the root
worktree, where deploy and the benchmark read their state. While a main is declared,
scripts/tool-guard.py refuses deploy, benchmarks, isuscope records and the operation lock to every
other session, and scripts/bench-hook.py hands worktree proposals (scripts/proposal.py) to main
alone. With no main declared nothing is refused, as before /main existed.
"""
import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

SCRIPTS = Path(__file__).resolve().parent


def git(*args, cwd=None):
    result = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ''


def root_repo(cwd=None):
    """The root repository (main worktree), seen from any of its worktrees."""
    common = git('rev-parse', '--path-format=absolute', '--git-common-dir', cwd=cwd)
    return Path(common).parent if common else SCRIPTS.parent


def toplevel(cwd=None):
    top = git('rev-parse', '--show-toplevel', cwd=cwd)
    return Path(top).resolve() if top else None


def main_branch(root):
    """The branch main works on: whatever the root worktree has checked out."""
    return git('symbolic-ref', '--quiet', '--short', 'HEAD', cwd=root) or git('rev-parse', 'HEAD', cwd=root)


def path(root):
    return Path(root) / '.local/main-session.json'


def load(root):
    try:
        data = json.loads(path(root).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get('session') else None


def current_session(agent=None):
    """The session running this, from the variables isuscope reads."""
    claude = os.environ.get('CLAUDE_CODE_SESSION_ID', '').strip()
    codex = os.environ.get('CODEX_SESSION_ID', '').strip() or os.environ.get('CODEX_THREAD_ID', '').strip()
    if agent == 'codex' or (agent is None and codex and not claude):
        return 'codex', codex
    return 'claude', claude


def is_main(root, session):
    """True for main, False for another session, None when no main is declared or the session is unknown."""
    declared = load(root)
    if not declared or not session:
        return None
    return declared['session'] == session


def write(root, data):
    target = path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(target)


def claim(root, agent, session, cwd=None):
    if not session:
        raise SystemExit('セッションIDが分からない。--session <実セッションID>で指定する。')
    here = toplevel(cwd)
    if here and here != Path(root).resolve():
        raise SystemExit(f'mainはrepoのroot（{root}）で動かす。ここ（{here}）はworktreeなので、'
                         'worktreeの変更は`python3 scripts/proposal.py submit`でmainへ渡す。')
    previous = load(root)
    data = {'agent': agent, 'session': session, 'branch': main_branch(root),
            'claimed_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
    if previous and previous['session'] != session:
        data['previous'] = {key: previous.get(key) for key in ('agent', 'session', 'claimed_at')}
    elif previous:
        data['claimed_at'] = previous.get('claimed_at', data['claimed_at'])
    write(root, data)
    lines = [f'mainは{agent}のセッション{session}（branch {data["branch"]}）。']
    if 'previous' in data:
        lines.append(f'前のmain（{previous.get("agent")} {previous["session"]}、{previous.get("claimed_at")}から）'
                     'から引き継いだ。前のmainはもうdeploy・ベンチ・isuscopeの記録・操作lockを使えない。')
    lines.append('ほかのセッションはdeploy・ベンチ・isuscopeの記録・操作lockを使えない。'
                 'worktreeの変更は投稿（scripts/proposal.py）として、ベンチを起動したときに渡される。')
    return '\n'.join(lines)


def release(root, session, force=False):
    declared = load(root)
    if not declared:
        return 'mainは宣言されていない。'
    if declared['session'] != session and not force:
        raise SystemExit(f'mainは別のセッション（{declared["agent"]} {declared["session"]}）。外すなら--force。')
    path(root).unlink()
    return 'mainの宣言を外した。'


BENCH_OPERATIONS = {'isuscope run': 'run', 'isuscope survey-run': 'survey-run'}


def benchmark(root):
    """The benchmark holding the operation lock now, read the way the board does, or None."""
    spec = importlib.util.spec_from_file_location('board_activity', SCRIPTS / 'board' / 'activity.py')
    activity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(activity)
    held = activity.operation(Path(root)) or {}
    kind = BENCH_OPERATIONS.get(held.get('operation'))
    if not kind:
        return None
    try:
        elapsed = max(0, int(time.time() - datetime.strptime(held['started_at'], '%Y-%m-%dT%H:%M:%S%z').timestamp()))
    except (KeyError, TypeError, ValueError):
        elapsed = None
    return {'kind': kind, 'started_at': held.get('started_at'), 'elapsed_seconds': elapsed}


def badge(root, session, cwd=None):
    """What the Claude Code mod draws above the prompt: this session's role, what waits for it,
    and the benchmark running now."""
    spec = importlib.util.spec_from_file_location('proposal', SCRIPTS / 'proposal.py')
    proposal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proposal)
    declared = load(root)
    here = toplevel(cwd)
    branch = git('symbolic-ref', '--quiet', '--short', 'HEAD', cwd=cwd) or git('rev-parse', '--short', 'HEAD', cwd=cwd)
    result = {'branch': branch, 'worktree': str(here or ''), 'is_root': here == Path(root).resolve(),
              'main': declared}
    if not declared:
        result['role'] = 'unclaimed'
    elif session and declared['session'] == session:
        result['role'] = 'main'
    else:
        result['role'] = 'other'
    items = proposal.load_all(root)
    result['waiting'] = sum(1 for item in items if item.get('status') in proposal.WAITING)
    own = next((item for item in items if item.get('branch') == branch and not result['is_root']), None)
    result['proposal'] = {key: own.get(key) for key in ('status', 'commit', 'decision')} if own else None
    result['benchmark'] = benchmark(root)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['claim', 'show', 'release', 'badge'])
    parser.add_argument('--agent', choices=['claude', 'codex'])
    parser.add_argument('--session')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    root = root_repo()
    agent, session = current_session(args.agent)
    session = (args.session or session or '').strip()
    if args.action == 'claim':
        print(claim(root, agent, session))
    elif args.action == 'release':
        print(release(root, session, args.force))
    elif args.action == 'badge':
        print(json.dumps(badge(root, session), ensure_ascii=False))
    else:
        declared = load(root)
        print(json.dumps(declared, ensure_ascii=False, indent=2) if declared else 'mainは宣言されていない。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
