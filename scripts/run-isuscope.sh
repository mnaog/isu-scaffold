#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
command -v isuscope >/dev/null
test -x "${script_dir}/isuscope-bin/ssh"

PATH="${script_dir}/isuscope-bin:${PATH}" exec isuscope "$@"
