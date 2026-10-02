#!/usr/bin/env python3
"""Run post-hoc order regression check on an isolated copy; keep AI results untouched."""
import argparse
import fcntl
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import manage as m

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--destination', type=Path, required=True)
a = p.parse_args()
dest = a.destination.resolve()
manifest = m.trial_manifest(dest)
head = m.output(['git', '-C', dest, 'rev-parse', 'HEAD'])
scratch = m.ROOT / '.local/ai-compare/order-audit' / manifest['trial']
if scratch.exists():
    raise SystemExit('Audit exists; preserve evidence')
archive = subprocess.check_output(['git', '-C', str(dest), 'archive', head])
scratch.mkdir(parents=True)
with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
    tar.extractall(scratch, filter='data')
(scratch / '.local').mkdir()
shutil.copyfile(dest / '.local/environment.json', scratch / '.local/environment.json')
m.execute(['/bin/cp', '-cR', dest / '.local/target', scratch / '.local/target'])
extra = m.ROOT / 'config/ai-compare/medium_ordering.rs'
f = scratch / 'webapp/rust/src/medium_acceptance.rs'
f.write_text(f.read_text() + '\n' + extra.read_text())
cfg = json.loads((scratch / '.local/environment.json').read_text())
env = m.build_environment(cfg['build_env'])
env.update(CARGO_TARGET_DIR=str(scratch / '.local/target'), ISUCON_CACHE_TEST_DATABASE_URL=cfg['database_url'])
command = [cfg['cargo'], '+' + cfg['toolchain'], 'test', '--locked', '--offline', 'medium_ordering_audit', '--', '--ignored', '--test-threads=1', '--nocapture']
with open(cfg['build_lock'], 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    started = time.time()
    with (scratch / '.local/result.log').open('w') as log:
        code = subprocess.run(command, cwd=scratch / 'webapp/rust', env=env, stdout=log, stderr=subprocess.STDOUT).returncode
report = {'trial': manifest['trial'], 'head': head, 'exit_code': code,
          'test_sha256': m.digest(extra), 'elapsed_seconds': time.time()-started,
          'log': str(scratch / '.local/result.log'),
          'note': 'Post-hoc audit; not part of the original task score. SQL has no explicit ORDER BY; this checks observed response equality on pinned MySQL.'}
m.write_json(dest / '.local/ordering-audit.json', report)
print(json.dumps(report, indent=2))
sys.exit(code)
