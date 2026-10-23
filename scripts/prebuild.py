#!/usr/bin/env python3
"""Build and test, while a benchmark runs, what main is likely to deploy after it.

After a run main decides on the proposals it measured, then reverts the rejected ones and merges the
next one, builds, tests and deploys. The build and the tests take seconds that main waits for, while
the machine sat idle for the whole benchmark. The benchmark hook starts this in the background after
`proposal.py sync`: in a worktree of its own (.local/prebuild, detached), it makes the trees main may
end up with and builds each with scripts/local-build.py, which keeps the binary and the test result
by source tree in the shared cache. If main's merge gives the same tree, `make deploy` neither builds
nor tests again. `proposal.py submit` also starts it for the proposal just submitted (--proposal), since
most proposals arrive while a benchmark already runs; builds never wait for each other
(scripts/local-build.py), so this never holds up main's deploy.

The trees: the branch as measured ("keep") and with every measured proposal reverted ("drop"), each
alone and with one waiting proposal merged (`git merge --no-ff`), the oldest waiting ones first, at
most MAX_VARIANTS builds. Other combinations are simply built when main makes them.

Nothing here touches main's worktree, the branches or the proposals; the commits stay detached in
.local/prebuild. A waiting proposal that conflicts, fails to build or fails its tests on top of main's
branch is reported on `.local/progress.log` (`prebuild failed`), before main merges it. The results
of the last run are in .local/prebuild.json (with a few earlier ones). PROPOSAL_AUTO_PREBUILD=0 turns it off.
"""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

SCRIPTS = Path(__file__).resolve().parent
MAX_VARIANTS = 6
LOCK_WAIT_SECONDS = 600
HISTORY = 10


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proposal = load('proposal', 'proposal.py')


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)


def root_of(path):
    common = git('rev-parse', '--path-format=absolute', '--git-common-dir', cwd=path).stdout.strip()
    return Path(common).parent


def measured_merges(root, head, measuring):
    """The merge commits on main's branch that brought in the proposals being measured, newest first."""
    tips = {}
    for data in measuring:
        tip = git('rev-parse', '--verify', '-q', f"refs/heads/{data['branch']}", cwd=root).stdout.strip()
        tips[tip or data.get('commit', '')] = data['branch']
    found = []
    log = git('log', '--first-parent', '--merges', '--format=%H %P', '-n', '200', head, cwd=root).stdout
    for line in log.splitlines():
        commit, *parents = line.split()
        if len(parents) >= 2 and parents[1] in tips:
            found.append((commit, tips.pop(parents[1])))
        if not tips:
            break
    return found


def variants(merges, waiting):
    starts = [('keep', [])] + ([('drop', merges)] if merges else [])
    plans = []
    for candidate in [*waiting[:1], None, *waiting[1:]]:
        for name, reverts in starts:
            plans.append({'start': name, 'reverts': reverts, 'merge': candidate})
    return plans[:MAX_VARIANTS]


def build(work, plan, head):
    """Make one tree in the work tree and build it; the result for .local/prebuild.json."""
    label = plan['start'] + (f"+{plan['merge']['branch']}" if plan['merge'] else '')
    result = {'variant': label, 'proposal': plan['merge']['branch'] if plan['merge'] else None}
    for step in (('reset', '--quiet', '--hard', head), ('clean', '-fdq')):
        done = git(*step, cwd=work)
        if done.returncode != 0:
            return {**result, 'status': 'error', 'detail': done.stderr.strip()}
    for commit, branch in plan['reverts']:
        done = git('revert', '--no-edit', '-m', '1', commit, cwd=work)
        if done.returncode != 0:
            git('revert', '--abort', cwd=work)
            return {**result, 'status': 'error', 'detail': f'cannot revert {branch}: {done.stderr.strip()}'}
    if plan['merge']:
        branch = plan['merge']['branch']
        done = git('merge', '--quiet', '--no-ff', '--no-edit', f'refs/heads/{branch}', cwd=work)
        if done.returncode != 0:
            conflicts = git('diff', '--name-only', '--diff-filter=U', cwd=work).stdout.split()
            git('merge', '--abort', cwd=work)
            return {**result, 'status': 'conflict', 'detail': ', '.join(conflicts) or done.stderr.strip()}
    result['tree'] = git('rev-parse', 'HEAD^{tree}', cwd=work).stdout.strip()
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as scratch:
        built = subprocess.run([sys.executable, str(work / 'scripts/local-build.py'), 'build', '--manifest',
                                str(work / 'config/sync.json'), '--index', str(Path(scratch) / 'index.json')],
                               cwd=work, capture_output=True, text=True, timeout=600)
    result['seconds'] = round(time.monotonic() - started, 1)
    if built.returncode != 0:
        detail = '\n'.join(built.stderr.strip().splitlines()[-12:])
        status = 'test-failed' if 'go test failed' in built.stderr else 'build-failed'
        return {**result, 'status': status, 'detail': detail}
    return {**result, 'status': 'ok', 'cached': 'local build: cached' in built.stderr}


def wait_for_lock(lock):
    """One prebuild at a time in the shared work tree; a later one waits for it rather than giving up."""
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--proposal', help='build only this waiting proposal (on the measured branch kept and dropped)')
    args = parser.parse_args()
    if os.environ.get('PROPOSAL_AUTO_PREBUILD') == '0':
        return 0
    root = root_of(SCRIPTS)
    if not (root / 'config/sync.json').is_file():
        print('prebuild: no config/sync.json yet (before kickoff-apply); nothing to build')
        return 0
    local = root / '.local'
    local.mkdir(exist_ok=True)
    # The sync the hook started first moves the waiting proposals onto main's branch; wait for it.
    with (local / 'proposal-sync.lock').open('a') as sync_lock:
        fcntl.flock(sync_lock, fcntl.LOCK_EX)
    with (local / 'prebuild.lock').open('a') as lock:
        if not wait_for_lock(lock):
            print(f'prebuild: another prebuild held the work tree for {LOCK_WAIT_SECONDS}s; giving up')
            return 0
        base = proposal.main_session.main_branch(root)
        head = git('rev-parse', '--verify', base, cwd=root).stdout.strip()
        everything = proposal.load_all(root)
        measuring = [data for data in everything if data.get('status') == 'measuring']
        waiting = sorted((data for data in everything if data.get('status') in ('submitted', 'delivered')
                          and (not args.proposal or data['branch'] == args.proposal)),
                         key=lambda data: data.get('first_submitted_at') or '')
        if args.proposal and not waiting:
            print(f'prebuild: {args.proposal} is not waiting in the queue; nothing to build')
            return 0
        plans = variants(measured_merges(root, head, measuring), waiting)
        if args.proposal:
            plans = [plan for plan in plans if plan['merge']]
        work = local / 'prebuild'
        if not (work / '.git').exists():
            added = git('worktree', 'add', '--quiet', '--detach', str(work), head, cwd=root)
            if added.returncode != 0:
                print(f'prebuild: cannot add the worktree: {added.stderr.strip()}')
                return 1
        results = []
        for plan in plans:
            results.append(build(work, plan, head))
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
        git('reset', '--quiet', '--hard', head, cwd=work)
        record = {'base': base, 'head': head, 'proposal': args.proposal, 'finished_at': proposal.now(),
                  'results': results}
        path = local / 'prebuild.json'
        try:
            earlier = json.loads(path.read_text())
            history = [{key: value for key, value in earlier.items() if key != 'history'}, *earlier.get('history', [])]
        except (OSError, ValueError, AttributeError):
            history = []
        record['history'] = history[:HISTORY]
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
        # A waiting proposal that fails on main's branch as measured: main hears of it before merging.
        failed = {}
        for result in results:
            if result['proposal'] and result['status'] in ('conflict', 'build-failed', 'test-failed'):
                failed.setdefault(result['proposal'], result)
        for branch, result in failed.items():
            summary = ' '.join(result['detail'].split())[:240]
            proposal.progress_emit(root, 'prebuild', 'failed',
                                   f"{branch} on {head[:8]} ({result['variant']}): {result['status']}: {summary}")
        ok = sum(result['status'] == 'ok' for result in results)
        print(f'prebuild: {ok} of {len(results)} trees built and tested on {head[:8]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
