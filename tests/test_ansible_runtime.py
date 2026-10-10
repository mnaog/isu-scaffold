import importlib.util
from pathlib import Path
import unittest
spec = importlib.util.spec_from_file_location('runtime', Path(__file__).resolve().parents[1] / 'scripts/ansible-runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)

class RuntimeTests(unittest.TestCase):
    def test_old_and_new_targets(self):
        self.assertEqual(runtime.select([(3, 8), (3, 12)]), 'legacy')
        self.assertEqual(runtime.select([(3, 12), (3, 14)]), 'modern')
    def test_incompatible_inventory_requires_explicit_split(self):
        for versions in ([], [(3, 8), (3, 14)], [(3, 15)], [(2, 7)]):
            with self.assertRaises(ValueError):
                runtime.select(versions)
    def test_nested_groups(self):
        self.assertEqual(runtime.hosts({'hosts': {'one': {}}, 'children': {'app': {'hosts': {'two': {}}}}}), {'one', 'two'})

    def test_custom_interpreter_is_not_silently_misdetected(self):
        with self.assertRaises(ValueError):
            runtime.require_default_interpreter({'all': {'vars': {'ansible_python_interpreter': '/opt/python/bin/python'}}})
        runtime.require_default_interpreter({'all': {'vars': {'ansible_python_interpreter': '/usr/bin/python3'}}})
