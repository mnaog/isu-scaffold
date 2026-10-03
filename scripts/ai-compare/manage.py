#!/usr/bin/env python3
"""Recreate native low-task trials: setup, prepare, run and audit."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from verify import build_environment

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / '.local/ai-compare/native'
SPEC = json.loads((ROOT / 'config/ai-compare/low.json').read_text())


def execute(argv, **kwargs):
    return subprocess.run([str(a) for a in argv], check=True, **kwargs)


def output(argv, **kwargs):
    return subprocess.check_output([str(a) for a in argv], **kwargs).decode().strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trial_manifest(dest):
    matches = [json.loads(p.read_text()) for p in (STATE / 'trials').glob('*.json')]
    matches = [m for m in matches if m['destination'] == str(dest)]
    if len(matches) != 1:
        raise SystemExit('Expected one controller-side manifest for this destination')
    manifest = matches[0]
    if digest(dest / '.local/environment.json') != manifest['environment_file_sha256']:
        raise SystemExit('Trial environment configuration changed')
    return manifest


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def build_env():
    sdk = output(['xcrun', '--show-sdk-path'])
    return {'RUSTUP_HOME': str(STATE / 'rustup'), 'CARGO_HOME': str(STATE / 'cargo'),
            'PATH': str(STATE / 'cargo/bin') + ':/usr/bin:/bin:/usr/sbin:/sbin',
            'CARGO_BUILD_JOBS': str(SPEC['build_jobs']), 'CARGO_INCREMENTAL': '0',
            'SDKROOT': sdk, 'CC': output(['xcrun', '--find', 'clang']),
            'CXX': output(['xcrun', '--find', 'clang++']),
            'MACOSX_DEPLOYMENT_TARGET': '11.0', 'RUSTUP_AUTO_INSTALL': '0'}


def archive_source(source, dest):
    data = subprocess.check_output(['git', '-C', str(source), 'archive',
                                   SPEC['source_commit'], *SPEC['source_paths']])
    dest.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter='data')
    return hashlib.sha256(data).hexdigest()


def fingerprint(env):
    return {'os': output(['sw_vers']), 'machine': output(['uname', '-m']),
            'rustc': output([STATE / 'cargo/bin/rustc', '+' + SPEC['toolchain'] + '-' + SPEC['host'], '-Vv'], env=env),
            'cargo': output([STATE / 'cargo/bin/cargo', '+' + SPEC['toolchain'] + '-' + SPEC['host'], '-V'], env=env),
            'clang': output(['xcrun', 'clang', '--version']),
            'sdk': output(['xcrun', '--show-sdk-version']),
            'sdk_path': output(['xcrun', '--show-sdk-path']),
            'python': sys.version, 'spec': SPEC}


def setup(args):
    if sys.platform != 'darwin' or output(['sysctl', '-n', 'hw.optional.arm64']) != '1':
        raise SystemExit('This pinned environment requires an Apple Silicon Mac.')
    STATE.mkdir(parents=True, exist_ok=True)
    env = build_environment(build_env())
    installer = STATE / 'rustup-init'
    if not (STATE / 'cargo/bin/rustup').exists():
        # The rustup launcher is native ARM; the selected compiler host is x86_64.
        url = f"https://static.rust-lang.org/rustup/archive/{SPEC['rustup']}/aarch64-apple-darwin/rustup-init"
        urllib.request.urlretrieve(url, installer)
        expected = urllib.request.urlopen(url + '.sha256', timeout=30).read().decode().split()[0]
        if digest(installer) != expected:
            raise SystemExit('rustup-init checksum mismatch')
        installer.chmod(0o700)
        execute([installer, '-y', '--no-modify-path', '--profile', 'minimal',
                 '--default-host', SPEC['host'], '--default-toolchain', 'none'], env=env)
    rustup = STATE / 'cargo/bin/rustup'
    execute([rustup, 'set', 'default-host', SPEC['host']], env=env)
    execute([rustup, 'set', 'auto-self-update', 'disable'], env=env)
    for version in (SPEC['toolchain'], SPEC['vendor_toolchain']):
        execute([rustup, 'toolchain', 'install', version, '--profile', 'minimal', '--no-self-update'], env=env)
    seed = STATE / 'seed'
    if not seed.exists():
        archive_sha = archive_source(args.source, seed)
        write_json(STATE / 'source.json', {'archive_sha256': archive_sha, 'source_commit': SPEC['source_commit']})
    (seed / '.cargo').mkdir(exist_ok=True)
    cargo = STATE / 'cargo/bin/cargo'
    if not (STATE / 'vendor-ready.json').exists():
        # Modern Cargo only downloads locked dependencies. Rust 1.63 compiles them.
        with (seed / '.cargo/config.toml').open('w') as config:
            execute([cargo, '+' + SPEC['vendor_toolchain'] + '-' + SPEC['host'], 'vendor', '--locked', STATE / 'vendor'],
                    cwd=seed / 'webapp/rust', env=env, stdout=config)
        files = {str(p.relative_to(STATE / 'vendor')): digest(p)
                 for p in sorted((STATE / 'vendor').rglob('*')) if p.is_file()}
        write_json(STATE / 'vendor-ready.json', {'files': files})
    config = (seed / '.cargo/config.toml').read_text()
    (STATE / 'cargo-config.toml').write_text(config)
    env['CARGO_TARGET_DIR'] = str(STATE / 'seed-target')
    logs = {}
    for mode in ('check', 'test'):
        path = STATE / f'initial-{mode}.log'
        print(f'Warming dependencies and reproducing {mode} failure...', flush=True)
        with path.open('w') as log:
            result = subprocess.run([str(cargo), '+' + SPEC['toolchain'] + '-' + SPEC['host'], mode, '--locked', '--offline'],
                                    cwd=seed / 'webapp/rust', env=env, stdout=log, stderr=subprocess.STDOUT)
        text = path.read_text()
        if result.returncode != 101 or SPEC['expected_initial_error'] not in text:
            print(text[-12000:])
            raise SystemExit(f'Unexpected initial {mode} result: {result.returncode}; see {path}')
        logs[mode] = {'exit_code': result.returncode, 'sha256': digest(path)}
    write_json(STATE / 'environment.json', {'fingerprint': fingerprint(env), 'build_env': build_env(),
               'initial_logs': logs, 'vendor_manifest_sha256': digest(STATE / 'vendor-ready.json'),
               'source': json.loads((STATE / 'source.json').read_text())})
    print('Ready: native Rust 1.63 reproduces the expected error in check and test.')


def prepare(args):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', args.trial):
        raise SystemExit('trial must contain lowercase letters, digits and hyphens')
    if (STATE / 'trials' / (args.trial + '.json')).exists():
        raise SystemExit('Trial ID already registered; use a new ID')
    saved = json.loads((STATE / 'environment.json').read_text())
    if fingerprint(build_environment(saved['build_env'])) != saved['fingerprint']:
        raise SystemExit('Host/toolchain changed. Run setup and start a new comparison batch.')
    dest = args.destination.resolve() if args.destination else ROOT.parent / 'ai-low-trials' / args.trial
    if dest.exists():
        raise SystemExit(f'Refusing to overwrite {dest}; use a new trial ID/destination.')
    archive_sha = archive_source(args.source, dest)
    if archive_sha != saved['source']['archive_sha256']:
        raise SystemExit('Source archive changed')
    (dest / '.local').mkdir(mode=0o700)
    (dest / '.cargo').mkdir()
    (dest / '.claude/rules').mkdir(parents=True)
    (dest / '.claude/rules/agents.md').symlink_to('../../AGENTS.md')
    for src, target in [('config/ai-compare/AGENTS.md', 'AGENTS.md'),
                        ('config/ai-compare/low-task.txt', 'TASK.txt'),
                        ('scripts/ai-compare/verify.py', 'verify.py')]:
        shutil.copyfile(ROOT / src, dest / target)
    shutil.copyfile(STATE / 'cargo-config.toml', dest / '.cargo/config.toml')
    (dest / 'rust-toolchain.toml').write_text(f'[toolchain]\nchannel = "{SPEC["toolchain"]}-{SPEC["host"]}"\nprofile = "minimal"\n')
    (dest / '.gitignore').write_text('/.local/\n/webapp/rust/target/\n__pycache__/\n')
    # Identical warm dependency state, private output for each trial.
    execute(['/bin/cp', '-cR', STATE / 'seed-target', dest / '.local/target'])
    config = {'trial': args.trial, 'toolchain': SPEC['toolchain'] + '-' + SPEC['host'], 'cargo': str(STATE / 'cargo/bin/cargo'),
              'build_env': saved['build_env'], 'build_lock': str(STATE / 'build.lock')}
    write_json(dest / '.local/environment.json', config)
    execute(['git', 'init', '-q', '-b', 'main', dest])
    with (dest / '.git/info/exclude').open('a') as exclude:
        exclude.write('\n# Generated history is evidence, not task source.\n/docs/agent-history/\n')
    for k, v in [('user.name', 'AI comparison'), ('user.email', 'ai-compare@localhost'),
                 ('commit.gpgsign', 'false')]:
        execute(['git', '-C', dest, 'config', k, v])
    execute(['git', '-C', dest, 'add', '.'])
    env = dict(os.environ, GIT_AUTHOR_DATE='2026-10-01T00:00:00+00:00', GIT_COMMITTER_DATE='2026-10-01T00:00:00+00:00')
    execute(['git', '-C', dest, '-c', 'core.hooksPath=/dev/null', 'commit', '-q', '-m', 'Initial task snapshot'], env=env)
    # Warm final-path fingerprints too. No model is running during preparation.
    for mode in ('check', 'test'):
        with (dest / f'.local/preparation-{mode}.log').open('w') as log:
            result = subprocess.run([sys.executable, str(dest / 'verify.py'), mode], stdout=log, stderr=subprocess.STDOUT)
        if result.returncode != 101 or SPEC['expected_initial_error'] not in (dest / f'.local/preparation-{mode}.log').read_text():
            raise SystemExit(f'Unexpected initial failure in {dest}; inspect preparation-{mode}.log')
    (dest / '.local/validation.jsonl').rename(dest / '.local/preparation.jsonl')
    tracked = output(['git', '-C', dest, 'ls-files']).splitlines()
    manifest = {'trial': args.trial, 'destination': str(dest), 'environment': saved,
                'environment_file_sha256': digest(dest / '.local/environment.json'),
                'source_archive_sha256': archive_sha, 'task_commit': output(['git', '-C', dest, 'rev-parse', 'HEAD']),
                'files': {p: digest(dest / p) for p in tracked}, 'prepared_at': time.time()}
    write_json(dest / '.local/manifest.json', manifest)
    (STATE / 'trials').mkdir(exist_ok=True)
    write_json(STATE / 'trials' / (args.trial + '.json'), manifest)
    print(dest)


def run_trial(args):
    dest = args.destination.resolve()
    manifest = trial_manifest(dest)
    if (dest / '.local/run.json').exists():
        raise SystemExit('Trial already started; prepare a new trial instead of reusing a conversation.')
    if output(['git', '-C', dest, 'status', '--porcelain']) or output(['git', '-C', dest, 'rev-parse', 'HEAD']) != manifest['task_commit']:
        raise SystemExit('Trial does not match its clean initial commit')
    if fingerprint(build_environment(manifest['environment']['build_env'])) != manifest['environment']['fingerprint']:
        raise SystemExit('Host/toolchain changed since preparation')
    argv = args.argv[1:] if args.argv and args.argv[0] == '--' else args.argv
    if not argv:
        raise SystemExit('Pass the agent command after --')
    env = dict(os.environ, **manifest['environment']['build_env'])
    env['PATH'] = str(STATE / 'cargo/bin') + os.pathsep + os.environ.get('PATH', '')
    env['PWD'] = str(dest)
    env.pop('OLDPWD', None)
    record = {'trial': manifest['trial'], 'agent': args.agent, 'model': args.model,
              'endpoint': args.endpoint, 'reasoning': args.reasoning, 'harness': args.harness,
              'argv': argv, 'started_at': time.time(), 'state': 'running'}
    record['settings_sha256'] = {str(p.resolve()): digest(p) for p in args.settings}
    executable = shutil.which(argv[0], path=env['PATH'])
    if executable is None:
        raise SystemExit(f'Command not found: {argv[0]}')
    record['executable'] = executable
    record['executable_sha256'] = digest(Path(executable))
    write_json(dest / '.local/run.json', record)
    tick = time.monotonic()
    try:
        # Interactive TTY stays attached. Each agent keeps its native session history.
        result = subprocess.run(argv, cwd=dest, env=env)
        record.update(exit_code=result.returncode, state='exited')
    except BaseException as e:
        record.update(state='interrupted', error=type(e).__name__)
        raise
    finally:
        record.update(ended_at=time.time(), elapsed_seconds=time.monotonic() - tick,
                      head=output(['git', '-C', dest, 'rev-parse', 'HEAD']),
                      dirty=bool(output(['git', '-C', dest, 'status', '--porcelain'])))
        write_json(dest / '.local/run.json', record)
    if record['exit_code']:
        raise SystemExit(record['exit_code'])


def audit(args):
    dest = args.destination.resolve()
    manifest = trial_manifest(dest)
    changed = [p for p, sha in manifest['files'].items() if not (dest / p).exists() or digest(dest / p) != sha]
    forbidden = [p for p in changed if not p.startswith('webapp/rust/src/') or p in manifest.get('protected_source', [])]
    if manifest.get('protected_source'):
        integration = dest / 'webapp/rust/src/new_user_cache_integration.rs'
        if integration.read_text().count('include!("medium_acceptance.rs");') != 1:
            forbidden.append('acceptance include changed')
    tracked = set(output(['git', '-C', dest, 'ls-files']).splitlines())
    extra = list(tracked - set(manifest['files']))
    extra += output(['git', '-C', dest, 'ls-files', '--others', '--exclude-standard']).splitlines()
    forbidden.extend(p for p in extra if not (manifest.get('protected_source') and p.startswith('webapp/rust/src/') and p.endswith('.rs')))
    if forbidden:
        raise SystemExit('Protected/untracked files changed: ' + ', '.join(forbidden))
    if fingerprint(build_environment(manifest['environment']['build_env'])) != manifest['environment']['fingerprint']:
        raise SystemExit('Host/toolchain changed since preparation')
    results = {}
    if manifest.get('database'):
        db = ROOT / '.local/ai-compare/mysql'
        if digest(db / 'my.cnf') != manifest['database']['config_sha256'] or digest(db / manifest['database']['spec']['mysql_archive'] / 'bin/mysqld') != manifest['database']['mysqld_sha256']:
            raise SystemExit('MySQL changed since preparation')
    for mode in manifest.get('validation_modes', ['check', 'test']):
        with (dest / f'.local/audit-{mode}.log').open('w') as log:
            # Use the controller's verifier, not code controlled by the trial agent.
            code = 'import sys; from pathlib import Path; from verify import validate; sys.exit(validate(Path(sys.argv[1]), sys.argv[2]))'
            results[mode] = subprocess.run([sys.executable, '-c', code, str(dest), mode],
                cwd=ROOT / 'scripts/ai-compare', stdout=log, stderr=subprocess.STDOUT).returncode
    report = {'changed_files': changed, 'forbidden_changes': forbidden, 'validation': results,
              'head': output(['git', '-C', dest, 'rev-parse', 'HEAD']),
              'dirty': bool(output(['git', '-C', dest, 'status', '--porcelain'])),
              'note': 'Review diff for behavior, test preservation (only task-authorized expectation updates), and active acceptance inclusion; compilation alone is insufficient.'}
    write_json(dest / '.local/audit.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if forbidden or any(results.values()) or report['dirty'] or report['head'] == manifest['task_commit']:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    for name in ('setup', 'prepare'):
        p = subs.add_parser(name)
        p.add_argument('--source', type=Path, default=ROOT.parent / 'practice-12')
        if name == 'prepare':
            p.add_argument('--trial', required=True)
            p.add_argument('--destination', type=Path)
    p = subs.add_parser('run')
    p.add_argument('--destination', type=Path, required=True)
    for option in ('agent', 'model', 'endpoint', 'reasoning', 'harness'):
        p.add_argument('--' + option, required=True)
    p.add_argument('--settings', type=Path, action='append', default=[], help='Hash a settings file without copying its contents')
    p.add_argument('argv', nargs=argparse.REMAINDER)
    p = subs.add_parser('audit')
    p.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    if args.command in ('setup', 'prepare') and os.environ.get('ISUSCOPE_LOCK_HELD') != '1':
        os.execvp('isuscope', ['isuscope', 'lock', '--path', str(ROOT / '.local/operation.lock'),
                              '--', sys.executable, *sys.argv])
    {'setup': setup, 'prepare': prepare, 'run': run_trial, 'audit': audit}[args.command](args)


if __name__ == '__main__':
    main()
