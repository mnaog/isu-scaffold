#!/usr/bin/env bash
# Bounded batches that work with macOS Bash 3.2 (empty arrays are unbound under set -u).
# A failure is remembered and reported by the final parallel_wait after every child finished.
parallel_init() {
  parallel_limit=$1
  [[ "${parallel_limit}" =~ ^[1-9][0-9]*$ ]] || { echo "parallel limit must be positive" >&2; return 2; }
  parallel_pids=()
  parallel_failed=0
}
parallel_wait() {
  local pid
  for pid in ${parallel_pids[@]+"${parallel_pids[@]}"}; do
    wait "${pid}" || parallel_failed=1
  done
  parallel_pids=()
  test "${parallel_failed}" -eq 0
}
parallel_start() {
  # Children must not consume the caller's while-read input.
  "$@" </dev/null &
  parallel_pids+=("$!")
  if [[ "${#parallel_pids[@]}" -ge "${parallel_limit}" ]]; then
    parallel_wait || true
  fi
}
