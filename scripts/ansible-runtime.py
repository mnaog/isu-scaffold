#!/usr/bin/env python3
"""Select a prepared controller from target Python versions, before gathering facts."""
import json
import os
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def select(versions):
    if not versions:
        raise ValueError('no target Python versions found')
    if all((3, 9) <= v <= (3, 14) for v in versions):
        return 'modern'
    if all((3, 8) <= v <= (3, 13) for v in versions):
        return 'legacy'
    raise ValueError('no common controller supports these targets; split the inventory by Python version')


def hosts(group):
    result = set(group.get('hosts', {}))
    for child in group.get('children', {}).values():
        result.update(hosts(child))
    return result


def require_default_interpreter(value):
    if isinstance(value, dict):
        interpreter = value.get('ansible_python_interpreter', '/usr/bin/python3')
        if interpreter != '/usr/bin/python3':
            raise ValueError('automatic selection requires /usr/bin/python3; verify custom inventory interpreters separately')
        for child in value.values():
            require_default_interpreter(child)
    elif isinstance(value, list):
        for child in value:
            require_default_interpreter(child)


def main():
    inventory = Path(os.environ.get('ANSIBLE_INVENTORY', ROOT / '.local/ansible-inventory.json'))
    data = json.loads(inventory.read_text())
    require_default_interpreter(data)
    if os.environ.get('ANSIBLE_PYTHON_INTERPRETER', '/usr/bin/python3') != '/usr/bin/python3':
        raise ValueError('automatic selection requires ANSIBLE_PYTHON_INTERPRETER=/usr/bin/python3')
    versions = {}
    for node in sorted(hosts(data['all'])):
        # The benchmark node also gets Linux prerequisites, so its Python decides the profile too.
        result = subprocess.run([str(ROOT / 'scripts/ssh-node.sh'), node,
                                 "/usr/bin/python3 -c 'import sys; print(str(sys.version_info[0])+\".\"+str(sys.version_info[1]))'"],
                                check=True, capture_output=True, text=True, timeout=30,
                                env={**os.environ, 'ISUCON_ALLOW_BENCHMARK_NODE': '1'})
        versions[node] = tuple(map(int, result.stdout.strip().split('.')))
    profile = select(list(versions.values()))
    requested = os.environ.get('ANSIBLE_PROFILE')
    if requested:
        limits = {'legacy': ((3, 8), (3, 13)), 'modern': ((3, 9), (3, 14))}
        if requested not in limits or not all(limits[requested][0] <= v <= limits[requested][1] for v in versions.values()):
            raise ValueError('explicit ANSIBLE_PROFILE does not support the target Python versions')
        profile = requested
    path = ROOT / '.local/ansible-runtime.env'
    path.parent.mkdir(exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text('ANSIBLE_PROFILE=' + shlex.quote(profile) + '\n')
    temporary.replace(path)
    print('Ansible:', profile, json.dumps(versions))


if __name__ == '__main__':
    main()
