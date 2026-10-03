#!/usr/bin/env python3
"""Create a reviewed contract only for the synthetic adapter integration fixture."""
import importlib.util
import json
import os
from pathlib import Path

root = Path.cwd()
spec = importlib.util.spec_from_file_location('contract', root / 'scripts/benchmark-contract.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
source = root / 'docs/official/fixture.txt'
source.parent.mkdir(parents=True, exist_ok=True)
source.write_text('Synthetic adapter test. No actual contest or server.')
value = {'schema_version': 1, 'reviewed_by': 'automated synthetic fixture', 'reviewed_at': 'fixture',
         'conditions': dict.fromkeys(['mode', 'request_timeout', 'initialize_timeout', 'load_duration', 'target'], 'synthetic fixture'),
         'sources': [{'path': 'docs/official/fixture.txt', 'sha256': m.digest(source.read_bytes())}],
         'invocation': {'executable': 'printf', 'required_flags': {}}}
value['effective_sha256'] = m.check(root, value, os.environ, seal=False)
(root / 'config/benchmark-contract.json').write_text(json.dumps(value))
