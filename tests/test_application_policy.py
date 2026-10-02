import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("practice_plan", ROOT / "scripts/check-practice-plan.py")
plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan)


class ApplicationPolicyTests(unittest.TestCase):
    @unittest.skipUnless((ROOT / "scripts/create-practice-stack.sh").exists(), "practice-only AWS entrypoint")
    def test_legacy_stack_is_rejected_before_any_aws_call(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            for name in ("scripts", "config", "infra", "bin"):
                (repo / name).mkdir()
            for name in ("create-practice-stack.sh", "application-policy.sh", "check-practice-plan.py"):
                shutil.copy2(ROOT / "scripts" / name, repo / "scripts" / name)
            shutil.copy2(ROOT / "config/application.env", repo / "config/application.env")
            shutil.copy2(ROOT / "infra/practice-stack.json", repo / "infra/practice-stack.json")
            fake = repo / "bin/aws"
            fake.write_text('#!/bin/sh\ntouch aws-was-called\nexit 1\n')
            fake.chmod(0o755)
            env = dict(os.environ, ISUSCOPE_LOCK_HELD="1", PATH=str(repo / "bin") + ":" + os.environ["PATH"])
            result = subprocess.run(["bash", "scripts/create-practice-stack.sh"], cwd=repo, env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"unfiltered official provisioning", result.stderr)
            self.assertFalse((repo / "aws-was-called").exists())

    def test_language_mismatch_is_rejected_before_import(self):
        result = subprocess.run(["bash", str(ROOT / "scripts/quick-import-code.sh"), "perl"],
                                env=dict(os.environ, ISUSCOPE_LOCK_HELD="1"), capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"must match config/application.env", result.stderr)

    def test_benchmarker_go_is_not_an_application_choice(self):
        template = {"Resources": {"Bench": {"Type": "AWS::EC2::Instance", "Properties": {
            "UserData": {"Fn::Base64": "ansible-playbook -i standalone, benchmarker.yml\n"}}}}}
        self.assertEqual(plan.check(template), [])
        template["Resources"]["Bench"]["Properties"]["UserData"]["Fn::Base64"] = {"Fn::Sub": "unreviewed"}
        self.assertTrue(plan.check(template))


if __name__ == "__main__":
    unittest.main()
