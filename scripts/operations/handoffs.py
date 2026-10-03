"""Validate newly published checkpoints and explicit integration receipts."""
import json
from pathlib import Path
import subprocess


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def ancestor(repo, a, b):
    return subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', a, b],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def changed(repo, base, head):
    return git(repo, 'diff', '--name-only', base, head, '--', 'webapp').splitlines()


def blob(repo, commit, name):
    result = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--verify', f'{commit}:{name}'],
                            text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 else None


def check_requirements(requirements):
    seen = set()
    for r in requirements:
        path = r['path']
        if not path.startswith('webapp/') or '..' in Path(path).parts or path in seen:
            raise ValueError('invalid or duplicate deployment requirement path')
        seen.add(path)
        if r.get('local_only_reason', '').strip():
            continue
        if not r.get('node_group') or not r.get('remote', '').startswith('/') or '..' in Path(r['remote']).parts:
            raise ValueError('deployment requirement needs node_group and absolute remote path')
        if 'command' in r and not r['command'].strip():
            raise ValueError('migration command cannot be empty')
    return seen


def check_manifest(requirements, manifest):
    check_requirements(requirements)
    for r in requirements:
        if r.get('local_only_reason'):
            continue
        matches = []
        for item in manifest['items']:
            local = item['local'].rstrip('/')
            if item['type'] == 'file' and r['path'] == local:
                remote = item['remote']
            elif item['type'] == 'directory' and r['path'].startswith(local + '/'):
                remote = item['remote'].rstrip('/') + r['path'][len(local):]
            else:
                continue
            if remote == r['remote'] and item['node_group'] == r['node_group']:
                matches.append(item)
        if len(matches) != 1:
            raise ValueError('missing or wrong deployment mapping: ' + r['path'])
        if r.get('command') and {'node_group': r['node_group'], 'command': r['command']} not in manifest.get('post_deploy_commands', []):
            raise ValueError('missing migration post_deploy_command: ' + r['path'])


def validate_new(db, repo, cwd, before):
    for h in db.execute('SELECT * FROM worker_handoffs'):
        if h['handoff_id'] in before[0]:
            continue
        worker = db.execute('SELECT * FROM workers WHERE task_id=?', (h['task_id'],)).fetchone()
        if git(cwd, 'rev-parse', 'HEAD') != h['source_commit'] or not ancestor(repo, h['base_commit'], h['source_commit']):
            raise ValueError('handoff must be the current committed HEAD, descended from its base')
        if Path(worker['worktree']).resolve() != Path(cwd).resolve():
            raise ValueError('only the assigned worktree can publish a handoff')
        if git(cwd, 'status', '--porcelain', '--untracked-files=all', '--', 'webapp'):
            raise ValueError('commit or set aside application changes before publishing a validated HEAD')
        req = json.loads(h['deployment_requirements'])
        declared = check_requirements(req)
        added = git(repo, 'diff', '--name-only', '--diff-filter=A', h['base_commit'], h['source_commit'], '--', 'webapp').splitlines()
        for name in added:
            if Path(name).suffix in ('.sql', '.sh') and name not in declared:
                raise ValueError('declare deployment or local_only_reason for new SQL/script: ' + name)
        db.execute('UPDATE workers SET result_commit=?,validation=? WHERE task_id=?',
                   (h['source_commit'], h['validation'], h['task_id']))
    for receipt in db.execute('SELECT * FROM worker_integrations'):
        if receipt['handoff_id'] in before[1]:
            continue
        h = db.execute('SELECT * FROM worker_handoffs WHERE handoff_id=?', (receipt['handoff_id'],)).fetchone()
        target = receipt['integration_commit']
        if target != git(repo, 'rev-parse', 'HEAD'):
            raise ValueError('integration receipt must identify current main HEAD')
        if not ancestor(repo, h['source_commit'], target):
            paths = changed(repo, h['base_commit'], h['source_commit'])
            if not paths or any(blob(repo, h['source_commit'], p) != blob(repo, target, p) for p in paths):
                raise ValueError('handoff not fully integrated; merge it or publish a separate validated subset')
        requirements = json.loads(h['deployment_requirements'])
        if requirements:
            manifest = json.loads(git(repo, 'show', target + ':config/sync.json'))
            check_manifest(requirements, manifest)
