#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=sync-lib.sh
source "${script_dir}/sync-lib.sh"
sync_validate
echo "sync manifest is valid"
