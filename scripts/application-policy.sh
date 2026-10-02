#!/usr/bin/env bash
# Read the adopted language from its single source of truth; never infer it from
# installed runtimes or a currently running reference implementation.
application_policy_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "${application_policy_dir}/config/application.env"
if [[ "${APPLICATION_LANGUAGE:-}" != rust ]]; then
  echo "application policy: this scaffold requires APPLICATION_LANGUAGE=rust" >&2
  return 2
fi
if [[ ! "${APPLICATION_PATH:-}" =~ ^webapp/[a-zA-Z0-9_./+-]+$ || "${APPLICATION_PATH}" == *".."* ]]; then
  echo "application policy: set a safe APPLICATION_PATH under webapp/" >&2
  return 2
fi
