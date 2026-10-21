#!/usr/bin/env python3
"""AI lanes: one `claude -p` run per lane, in its own worktree and branch, merged by main.

  lane.py start NAME [--prompt-file PATH --purpose TEXT] [--force]   start a lane in the background
  lane.py kickoff                                          start the lanes make kickoff runs
  lane.py status [NAME]                                    show lanes, or one lane's result
  lane.py stop NAME                                        stop a running lane

A lane creates `lane/<name>` from the current branch with scripts/worktree.sh, runs Claude
Code headless there with ISUCON_LANE set (tool-guard then refuses remote nodes, deploy,
benchmarks and the operation lock), commits its conversation log when Claude exits, and
records status.json, the stream and the final answer under .local/lanes/<name>/ of the
root repository. Milestones go to .local/progress.log. The main session reads the answer
and merges the branch; lanes never merge into other branches or push (a lane whose prompt
says so may merge the branch it started from into its own). config/lanes.json holds the
model, effort, timeouts, the named prompts and the tools a lane may use: permission mode
dontAsk denies everything else without asking, so an unattended lane cannot widen its own
access. A lane may list `requires`: files the starting commit must already hold, so a lane
that builds on earlier lanes cannot start before main has taken them in. A lane may instead list
`after`: lanes whose branches it builds on. It starts by itself when the last of them ends
(complete, timeout or stopped), from main's branch with their branches merged in, without
waiting for main to take them in; a branch that does not merge cleanly is left for the lane
to merge first. Their commits are not reported as the lane's. A lane may also list `joins`:
lanes that start alongside it and whose branches it merges itself once they end (it reads their
state with `lane.py status`), so it need not wait for them to begin; their commits are not
reported as the lane's either.
"""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'config/lanes.json'

_spec = importlib.util.spec_from_file_location('progress', Path(__file__).resolve().parent / 'progress.py')
progress = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(progress)


def git(*args, cwd=None, check=True):
    return subprocess.run(['git', *args], cwd=cwd or ROOT, capture_output=True, text=True, check=check).stdout.strip()


def root_repo():
    """State lives in the root repository even when a worktree calls this script."""
    try:
        return Path(git('rev-parse', '--path-format=absolute', '--git-common-dir')).parent
    except (OSError, subprocess.CalledProcessError):
        return ROOT


def state_dir(name):
    return root_repo() / '.local/lanes' / name


def load_config():
    return json.loads(CONFIG.read_text())


def valid_name(name):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,40}', name or ''):
        raise SystemExit(f'lane name must be lowercase letters, digits and hyphens: {name!r}')
    return name


def save(name, state, **fields):
    directory = state_dir(name)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'status.json'
    previous = json.loads(path.read_text()) if path.exists() else {}
    record = dict(previous, **fields, state=state, updated_at=time.time())
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    temp.replace(path)
    return record


def lane_settings(name, prompt_file=None, purpose=None):
    config = load_config()
    defined = config.get('lanes', {}).get(name, {})
    prompt = prompt_file or defined.get('prompt')
    purpose = purpose or defined.get('purpose')
    if not prompt or not purpose:
        raise SystemExit(f'lane {name} is not in config/lanes.json; give --prompt-file and --purpose')
    claude = dict(config.get('claude', {}))
    claude['timeout_minutes'] = defined.get('timeout_minutes', claude.get('timeout_minutes', 60))
    claude['allowed_tools'] = claude.get('allowed_tools', []) + defined.get('extra_allowed_tools', [])
    if defined.get('joins'):
        # The lane checks whether the lanes it joins have ended and merges their branches itself.
        claude['allowed_tools'] += [tool for tool in ('Bash(python3 scripts/lane.py status:*)', 'Bash(git merge:*)')
                                    if tool not in claude['allowed_tools']]
    return claude, Path(prompt), purpose


def missing_requirements(name):
    """Files the lane's definition requires that the commit it would start from lacks."""
    required = load_config().get('lanes', {}).get(name, {}).get('requires', [])
    return [path for path in required
            if subprocess.run(['git', 'cat-file', '-e', f'HEAD:{path}'], cwd=ROOT, capture_output=True).returncode]


def start(name, prompt_file=None, purpose=None, force=False):
    if os.environ.get('ISUCON_LANE'):
        raise SystemExit('a lane does not start other lanes; report the idea in its final answer')
    waiting = waiting_on(name)
    if waiting and not force:
        raise SystemExit(f'lane {name} starts by itself when these lanes end: {", ".join(waiting)} '
                         f'(--force starts it now with the branches they have)')
    claude, prompt, purpose = lane_settings(name, prompt_file, purpose)
    prompt = prompt if prompt.is_absolute() else ROOT / prompt
    if not prompt.is_file():
        raise SystemExit(f'prompt not found: {prompt}')
    missing = missing_requirements(name)
    if missing:
        raise SystemExit(f'lane {name} needs these files committed on the current branch first: {", ".join(missing)}')
    if not shutil.which(claude.get('command', 'claude')):
        progress.emit(f'lane:{name}', 'failed', f'{claude.get("command", "claude")} command not found', root=ROOT)
        raise SystemExit(1)
    directory = state_dir(name)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'lane.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
        except BlockingIOError:
            print(f'lane {name} is already running')
            return
    environment = os.environ.copy()
    environment.pop('ISUSCOPE_LOCK_HELD', None)
    with (directory / 'run.log').open('a') as log:
        child = subprocess.Popen([sys.executable, __file__, 'run', name, '--prompt-file', str(prompt),
                                  '--purpose', purpose], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=log, start_new_session=True, env=environment)
    print(f'lane {name} started (pid {child.pid}); status: python3 scripts/lane.py status {name}', flush=True)


def kickoff():
    configured = load_config().get('kickoff', [])
    requested = os.environ.get('KICKOFF_LANES')
    names = configured if requested is None else requested.replace(',', ' ').split()
    if not names:
        print('kickoff lanes disabled')
        return 0
    failed = 0
    for name in names:
        status = state_dir(valid_name(name)) / 'status.json'
        # kickoff is rerunnable; a lane that ran or still runs is not started twice.
        if status.exists() and json.loads(status.read_text()).get('state') in ('preparing', 'running', 'complete'):
            print(f'lane {name} already {json.loads(status.read_text())["state"]}; skipping')
            continue
        try:
            start(valid_name(name))
        except SystemExit as error:
            failed = 1
            if error.code not in (None, 0, 1):
                print(f'lane {name} not started: {error.code}', file=sys.stderr)
    return failed


DONE = ('complete', 'timeout', 'stopped')  # ended with whatever it committed


def lane_state(name):
    path = state_dir(name) / 'status.json'
    try:
        return json.loads(path.read_text()).get('state')
    except (OSError, ValueError):
        return None


def waiting_on(name):
    """The lanes named in `after` that have not ended yet."""
    after = load_config().get('lanes', {}).get(name, {}).get('after', [])
    return [lane for lane in after if lane_state(lane) not in DONE]


def start_followers(finished):
    """Start each lane that waits for `finished` once all its `after` lanes have ended."""
    for name, lane in load_config().get('lanes', {}).items():
        after = lane.get('after', [])
        if finished not in after or lane_state(name) is not None:
            continue
        failed = [other for other in after if lane_state(other) == 'failed']
        if failed:
            progress.emit(f'lane:{name}', 'blocked', f'{", ".join(failed)} failed; '
                          f'start it with python3 scripts/lane.py start {name} --force', root=ROOT)
            continue
        if waiting_on(name):
            continue
        try:
            start(name)
        except SystemExit as error:
            progress.emit(f'lane:{name}', 'failed', f'not started: {error.code}', root=ROOT)


def merge_after(name, worktree):
    """Merge the branches of the lanes this one builds on; return (merged, left for the lane)."""
    merged, left = [], []
    for lane in load_config().get('lanes', {}).get(name, {}).get('after', []):
        branch = f'lane/{lane}'
        if git('rev-parse', '--verify', '--quiet', branch, check=False) == '':
            continue
        result = subprocess.run(['git', 'merge', '--no-ff', '--no-edit', '-q', branch], cwd=worktree,
                                capture_output=True, text=True)
        if result.returncode:
            subprocess.run(['git', 'merge', '--abort'], cwd=worktree, capture_output=True)
            left.append(branch)
        else:
            merged.append(branch)
    return merged, left


def create_worktree(name, purpose):
    branch = f'lane/{name}'
    base = git('symbolic-ref', '--quiet', '--short', 'HEAD', check=False) or git('rev-parse', 'HEAD')
    directory = state_dir(name)
    # git worktree add and git config both lock files under .git; lanes start together.
    with (directory.parent / 'worktree.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = subprocess.run([str(ROOT / 'scripts/worktree.sh'), branch, purpose, base], cwd=ROOT,
                                capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'worktree: {(result.stderr or result.stdout).strip()}')
    path = next((line.split(':', 1)[1].strip() for line in result.stdout.splitlines() if line.startswith('worktree:')), None)
    if not path:
        raise RuntimeError('worktree.sh did not print the worktree path')
    return Path(path), branch, base, git('rev-parse', branch)


def lane_prompt(name, prompt, worktree, branch, base, base_commit, merged=(), left=(), joins=()):
    after = ''
    if merged:
        after += f'始める前に、lane.pyが{", ".join(merged)}をこのbranchへmergeした。'
    if left:
        after += (f'{", ".join(left)}は衝突したのでmergeしていない。最初に`git merge --no-edit <branch>`で取り込み、'
                  '両方の意図を残して衝突を解いてcommitする。')
    if joins:
        after += (f'同時に動いているlane {", ".join(joins)}のbranch（lane/<名前>）は、このlaneが自分で取り込む。'
                  '終わったかは`python3 scripts/lane.py status <名前>`の`state`で見る（complete・timeout・stoppedなら終わり）。'
                  '取り込む時機は下の指示に従う。')
    return (f'あなたはISUCONの並行lane「{name}」として、mainのセッションとは別に1回だけ動くClaude Codeです。\n'
            f'作業場所: {worktree}（branch {branch}、分岐元branch {base}、分岐元commit {base_commit[:12]}）。'
            '人間とmainのセッションは途中で返事をしない。確認を待たずに最後まで進め、判断が要るものは最後の回答に書く。\n'
            'このbranchへcommitだけを行い、他のbranchへのmerge・push・他のworktreeの変更・remote操作はしない'
            '（分岐元branchや他のlaneのbranchをこのbranchへ取り込むのは、ここか下の指示がそう求める場合だけ）。\n'
            + (after + '\n' if after else '') + '\n' + prompt.read_text())


def result_of(stream):
    """The final `result` event of a stream-json run, if Claude got that far."""
    final = None
    try:
        for line in stream.read_text(errors='replace').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get('type') == 'result':
                final = event
    except OSError:
        pass
    return final


def lane_commits(branch, base, base_commit, *options, built_on=()):
    """The lane's own commits: those the base branch already has (merged in by the lane) are main's,
    and those of the lanes it was built on (`after`) or joins (`joins`) are theirs."""
    # A joined lane's branch may not exist yet; git log refuses an unknown name in --not.
    present = [ref for ref in built_on if git('rev-parse', '--verify', '--quiet', ref, check=False)]
    return git('log', *options, '--format=%h %s', branch, '--not', base_commit, base, *present,
               check=False).splitlines()


def report_commits(source, name, base, base_commit, branch, reported, built_on=()):
    """Each commit is usable on its own, so main may cherry-pick it before the lane ends."""
    for line in lane_commits(branch, base, base_commit, '--reverse', built_on=built_on):
        if line and line not in reported:
            reported.add(line)
            progress.emit(source, 'commit', f'{line} (git cherry-pick {line.split()[0]})', root=ROOT)
    save(name, 'running', commits=sorted(reported))


def commit_history(worktree):
    """The agent-history hook keeps writing after Claude's last commit; keep it with the lane."""
    if git('status', '--porcelain', '--', 'docs/agent-history', cwd=worktree, check=False):
        git('add', '--', 'docs/agent-history', cwd=worktree, check=False)
        git('commit', '-q', '-m', 'Record the lane conversation', '--', 'docs/agent-history', cwd=worktree, check=False)


def record_timing(name, started, code):
    try:
        with (root_repo() / '.local/stage-timing.tsv').open('a') as out:
            stamp = time.strftime('%Y-%m-%dT%H:%M:%S%z', time.localtime(started))
            out.write(f'{stamp}\tlane\t{name}\t{time.time() - started:.1f}\t{code}\n')
    except OSError:
        pass


def run(name, prompt_file, purpose):
    claude, prompt, purpose = lane_settings(name, prompt_file, purpose)
    prompt = prompt if prompt.is_absolute() else ROOT / prompt
    directory = state_dir(name)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'lane.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f'lane {name} is already running', flush=True)
            return 0
        started = time.time()
        source = f'lane:{name}'
        save(name, 'preparing', pid=os.getpid(), started_at=started, purpose=purpose, model=claude.get('model'),
             effort=claude.get('effort'), result=None, error=None)
        code = 1
        try:
            worktree, branch, base, base_commit = create_worktree(name, purpose)
            merged, left = merge_after(name, worktree)
            defined = load_config().get('lanes', {}).get(name, {})
            joins = defined.get('joins', [])
            # Not the lane's own: the merges above, and what the lanes it builds on or joins committed.
            built_on = [git('rev-parse', branch)] + [f'lane/{lane}' for lane in defined.get('after', []) + joins]
            if merged or left:
                progress.emit(source, 'merged', ', '.join(merged) + (f'; left for the lane: {", ".join(left)}' if left else ''),
                              root=ROOT)
            command = [claude.get('command', 'claude'), '-p', '--output-format', 'stream-json', '--verbose',
                       '--model', claude['model'], '--effort', claude['effort'],
                       '--permission-mode', claude.get('permission_mode', 'dontAsk'),
                       '--allowedTools', ','.join(claude.get('allowed_tools', []))]
            # --allowedTools takes several values, so the prompt goes through stdin, not argv.
            text = lane_prompt(name, prompt, worktree, branch, base, base_commit, merged, left, joins)
            environment = dict(os.environ, ISUCON_LANE=name, ISUCON_LANE_ROOT=str(root_repo()))
            # Started from a main session that is itself Claude Code: run as a fresh top-level session.
            for variable in ('ISUSCOPE_LOCK_HELD', 'CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT'):
                environment.pop(variable, None)
            timeout = float(claude['timeout_minutes']) * 60
            save(name, 'running', worktree=str(worktree), branch=branch, base=base, base_commit=base_commit,
                 timeout_minutes=claude['timeout_minutes'], merged=merged, left_to_merge=left)
            progress.emit(source, 'running', f'{branch} at {worktree}', root=ROOT)
            stream = directory / 'stream.jsonl'
            with stream.open('w') as out, (directory / 'claude.stderr').open('w') as err:
                child = subprocess.Popen(command, cwd=worktree, env=environment, stdin=subprocess.PIPE,
                                         stdout=out, stderr=err, start_new_session=True)
                try:
                    child.stdin.write(text.encode())
                    child.stdin.close()
                except BrokenPipeError:
                    pass
                save(name, 'running', claude_pid=child.pid)
                deadline = time.monotonic() + timeout
                poll = float(os.environ.get('LANE_POLL_SECONDS', '15'))
                reported = set()
                timed_out = False
                while True:
                    try:
                        returncode = child.wait(timeout=max(0.1, min(poll, deadline - time.monotonic())))
                        break
                    except subprocess.TimeoutExpired:
                        report_commits(source, name, base, base_commit, branch, reported, built_on)
                        if time.monotonic() < deadline:
                            continue
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        returncode = child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        returncode = child.wait()
                    timed_out = True
                    break
            report_commits(source, name, base, base_commit, branch, reported, built_on)
            commit_history(worktree)
            final = result_of(stream) or {}
            answer = final.get('result') or ''
            (directory / 'result.md').write_text(answer + '\n')
            commits = lane_commits(branch, base, base_commit, built_on=built_on)
            ok = returncode == 0 and not timed_out and final and not final.get('is_error')
            code = 0 if ok else 1
            state = 'complete' if ok else ('timeout' if timed_out else 'failed')
            save(name, state, returncode=returncode, commits=commits, session_id=final.get('session_id'),
                 cost_usd=final.get('total_cost_usd'), turns=final.get('num_turns'),
                 seconds=round(time.time() - started, 1), result=str(directory / 'result.md'),
                 error=None if ok else (final.get('subtype') or f'exit {returncode}'))
            progress.emit(source, state, f'{len(commits)} commits on {branch}; result {directory / "result.md"}', root=ROOT)
        except Exception as error:  # the lane must always end in a recorded state
            save(name, 'failed', error=str(error), seconds=round(time.time() - started, 1))
            progress.emit(source, 'failed', str(error), root=ROOT)
        record_timing(name, started, code)
    start_followers(name)
    return code


def show(name=None):
    base = root_repo() / '.local/lanes'
    if name:
        path = base / name / 'status.json'
        if not path.exists():
            raise SystemExit(f'lane {name} has not started')
        print(path.read_text(), end='')
        result = base / name / 'result.md'
        if result.exists():
            print('\n--- result ---\n' + result.read_text(), end='')
        return
    rows = sorted(base.glob('*/status.json')) if base.exists() else []
    if not rows:
        print('no lanes')
        return
    for path in rows:
        status = json.loads(path.read_text())
        elapsed = status.get('seconds') or (time.time() - status.get('started_at', time.time()))
        print(f'{path.parent.name:16} {status["state"]:9} {elapsed / 60:5.1f}min '
              f'{len(status.get("commits") or []):2} commits  {status.get("branch", "")}  {status.get("error") or ""}')


def stop(name):
    directory = state_dir(name)
    with (directory / 'lane.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print(f'lane {name} is not running')
            return
        except BlockingIOError:
            pass
    status = json.loads((directory / 'status.json').read_text())
    claude = os.path.basename(load_config().get('claude', {}).get('command', 'claude'))
    # The runner first, so it cannot record a result after this; then Claude.
    for key, expected in (('pid', 'lane.py'), ('claude_pid', claude)):
        pid = status.get(key)
        if not pid:
            continue
        try:
            command = subprocess.check_output(['ps', '-p', str(pid), '-o', 'command='], text=True)
        except subprocess.CalledProcessError:
            continue
        # Never signal a reused PID: the runner and Claude both lead their own process group.
        if expected not in command or os.getpgid(pid) != pid:
            continue
        os.killpg(pid, signal.SIGTERM)
    save(name, 'stopped')
    progress.emit(f'lane:{name}', 'stopped', '', root=ROOT)
    start_followers(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['start', 'kickoff', 'run', 'status', 'stop'])
    parser.add_argument('name', nargs='?')
    parser.add_argument('--prompt-file')
    parser.add_argument('--purpose')
    parser.add_argument('--force', action='store_true', help='start a lane before the lanes in its `after` end')
    args = parser.parse_args()
    if args.action == 'kickoff':
        return kickoff()
    if args.action == 'status':
        return show(args.name and valid_name(args.name))
    name = valid_name(args.name)
    if args.action == 'start':
        return start(name, args.prompt_file, args.purpose, args.force)
    if args.action == 'stop':
        return stop(name)
    return run(name, args.prompt_file, args.purpose)


if __name__ == '__main__':
    sys.exit(main() or 0)
