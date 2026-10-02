#!/usr/bin/env python3
"""Reject the obsolete all-language provisioning path before creating AWS resources."""
import json
from pathlib import Path
import re
import sys


def check(template):
    errors = []
    for name, resource in template.get("Resources", {}).items():
        if resource.get("Type") != "AWS::EC2::Instance":
            continue
        script = resource.get("Properties", {}).get("UserData", {}).get("Fn::Base64")
        if not isinstance(script, str):
            errors.append(f"{name}: UserData must be a reviewable script")
            continue
        # The legacy base/application playbooks transitively install optional languages.
        # Rust adoption alone does not constrain these official provisioning roles.
        if re.search(r"ansible-playbook[^\n]*\b(?:base|application)\.yml\b", script):
            errors.append(f"{name}: unfiltered official provisioning may install Perl and other unused runtimes")
    return errors


if __name__ == "__main__":
    path = Path(sys.argv[1])
    errors = check(json.loads(path.read_text()))
    if errors:
        sys.exit("practice provisioning plan rejected before AWS changes:\n" + "\n".join(errors)
                 + "\nReplace legacy UserData with a reviewed role-specific setup plan first.")
