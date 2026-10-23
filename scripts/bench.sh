#!/usr/bin/env bash
set -uo pipefail

# The benchmark of what main merged, in the one call main starts in the background (make bench):
# deploy, then `isuscope run`, then that run's brief, so the result and the brief come back together
# and no turn of main's sits between the deploy and the benchmark. Merge before starting it: the
# benchmark hook marks what main merged as measuring when this starts.
#
#   make bench                                   the queue's hypothesis (proposal.py hypothesis)
#   scripts/bench.sh --repeat "振れ幅: ..." --hypothesis "..."   options go to `isuscope run`
#
# If the deploy fails, the benchmark does not start: the proposals the hook marked measuring go back
# to the queue, and the deploy's output is printed. The run's output stays in .local/bench-last.out
# and the deploy's in .local/deploy-last.out. Exits with the deploy's or the run's code.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
cd "${repo_dir}" || exit 2
mkdir -p .local
deploy_log=.local/deploy-last.out
bench_log=.local/bench-last.out

args=("$@")
has_hypothesis=0
for arg in "${args[@]+"${args[@]}"}"; do
  [[ "${arg}" == --hypothesis || "${arg}" == --hypothesis=* ]] && has_hypothesis=1
done
if [[ "${has_hypothesis}" == 0 ]]; then
  if ! hypothesis=$(python3 scripts/proposal.py hypothesis 2>&1); then
    printf '%s\n' "${hypothesis}" >&2
    echo 'bench: no hypothesis; pass --hypothesis "..." for a benchmark without a merged proposal' >&2
    exit 2
  fi
  args+=(--hypothesis "${hypothesis}")
fi

if ! make deploy >"${deploy_log}" 2>&1; then
  echo "bench: the deploy failed, so the benchmark did not start (${deploy_log}):"
  tail -n 20 "${deploy_log}"
  python3 scripts/proposal.py requeue 2>&1 || true
  exit 1
fi
echo "deploy: $(tail -n 1 "${deploy_log}")"

code=0
isuscope run "${args[@]}" >"${bench_log}" 2>&1 || code=$?
tail -n 12 "${bench_log}"
run=$(awk '$1 == "run" && NF == 2 {id = $2} END {print id}' "${bench_log}")
echo
isuscope brief "${run:-latest}" || true
exit "${code}"
