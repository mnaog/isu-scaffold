#!/usr/bin/env bash
# Stage timing inside one locked operation. `isuscope lock` records only the outermost
# operation in .local/operation-timing.tsv; nested scripts and their steps go here as
# started_at, operation, stage, seconds, exit. Works with macOS Bash 3.2 (no EPOCHREALTIME).
stage_timing_file=${STAGE_TIMING_FILE:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/.local/stage-timing.tsv}
# The first script that sources this names the operation; nested scripts keep it.
export STAGE_OPERATION=${STAGE_OPERATION:-$(basename -- "$0")}
stage_lib_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

stage_now() {
  python3 -c 'import time; print("%.3f" % time.time())'
}

# stage NAME COMMAND [ARGUMENTS ...]: run the command and record how long it took.
stage() {
  local name=$1 started code=0
  shift
  started=$(stage_now)
  "$@" || code=$?
  mkdir -p "$(dirname -- "${stage_timing_file}")"
  python3 - "${stage_timing_file}" "${started}" "$(stage_now)" "${STAGE_OPERATION}" "${name}" "${code}" <<'EOF' || true
import sys, time
path, started, ended, operation, name, code = sys.argv[1:]
at = time.strftime('%Y-%m-%dT%H:%M:%S%z', time.localtime(float(started)))
with open(path, 'a') as out:
    out.write(f'{at}\t{operation}\t{name}\t{float(ended) - float(started):.1f}\t{code}\n')
EOF
  if [[ "${code}" -ne 0 ]]; then
    # The agent following .local/progress.log learns of the failure without waiting for the end.
    python3 "${stage_lib_dir}/progress.py" emit "${STAGE_OPERATION}" failed "${name} (exit ${code})" >/dev/null 2>&1 || true
  fi
  return "${code}"
}
