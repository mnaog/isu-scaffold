#!/usr/bin/env bash
set -euo pipefail

# Stop the benchmark that is running now, after a `benchmark slow` line in .local/progress.log.
# It sends SIGTERM to the isuscope process the adapter recorded, so isuscope records the run as
# interrupted, stops the benchmark's process group and runs cleanup_command, as for its own deadline.

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_root=$(cd -- "${script_dir}/.." && pwd)
run_pid_file=${project_root}/.local/benchmark-run.pid

[[ -f "${run_pid_file}" ]] || { echo "no running benchmark (${run_pid_file} is missing)" >&2; exit 1; }
pid=$(tr -d '[:space:]' <"${run_pid_file}")
args=$(ps -o args= -p "${pid}" 2>/dev/null || true)
if [[ ! "${args}" =~ isuscope[[:space:]]+(run|survey-run)([[:space:]]|$) ]]; then
  echo "process ${pid} is not a running isuscope run; nothing stopped" >&2
  rm -f -- "${run_pid_file}"
  exit 1
fi
kill -TERM "${pid}"
echo "sent SIGTERM to isuscope (pid ${pid}); it records the run as interrupted and cleans up the benchmark"
