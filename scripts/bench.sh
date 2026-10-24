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
# It ends with what main decides on (scripts/bench-result.py): the facts the brief does not say, then the
# brief, also in .local/bench-results/<run>.txt and sent to main's Monitor (`benchmark finished`), so main
# need not read this output first. Nothing starts while a PASS run still waits for its analysis (checked
# before the deploy). If the deploy fails, or isuscope starts no run, the benchmark does not start and that
# is reported the same way (`benchmark not-started`); the proposals the hook marked measuring go back to
# the queue. The run's output stays in .local/bench-last.out and the deploy's in .local/deploy-last.out.
# Exits with the deploy's or the run's code.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
cd "${repo_dir}" || exit 2
mkdir -p .local
deploy_log=.local/deploy-last.out
bench_log=.local/bench-last.out

# isuscope takes text that starts with '-' (a hypothesis such as "-2% ...") only as --option=value.
args=()
while [[ $# -gt 0 ]]; do
  if [[ ( "$1" == --hypothesis || "$1" == --repeat ) && $# -gt 1 ]]; then
    args+=("$1=$2")
    shift 2
  else
    args+=("$1")
    shift
  fi
done
has_hypothesis=0
for arg in "${args[@]+"${args[@]}"}"; do
  [[ "${arg}" == --hypothesis=* ]] && has_hypothesis=1
done
if [[ "${has_hypothesis}" == 0 ]]; then
  if ! hypothesis=$(python3 scripts/proposal.py hypothesis 2>&1); then
    printf '%s\n' "${hypothesis}" >&2
    echo 'bench: no hypothesis; pass --hypothesis "..." for a benchmark without a merged proposal' >&2
    exit 2
  fi
  args+=("--hypothesis=${hypothesis}")
fi

python3 scripts/bench-result.py check || exit 2
if ! make deploy >"${deploy_log}" 2>&1; then
  echo "bench: the deploy failed, so the benchmark did not start (${deploy_log}):"
  python3 scripts/proposal.py requeue 2>&1 || true
  python3 scripts/bench-result.py not-started "the deploy failed (${deploy_log})" --output "${deploy_log}"
  exit 1
fi
echo "deploy: $(tail -n 1 "${deploy_log}")"

code=0
isuscope run "${args[@]}" >"${bench_log}" 2>&1 || code=$?
run=$(awk '$1 == "run" && NF == 2 {id = $2} END {print id}' "${bench_log}")
if [[ -z "${run}" ]]; then
  # No run came of it (refused, or failed before one existed): the latest run's brief would read as this one's.
  python3 scripts/bench-result.py not-started "isuscope run started no run (exit ${code})" --output "${bench_log}"
  exit $((code == 0 ? 1 : code))
fi
# isuscope's own next step is `isuscope analyze`; main records with `proposal.py next` instead.
tail -n 12 "${bench_log}" | grep -v '^next '
echo
mkdir -p .local/bench-results
brief=.local/bench-results/${run}.brief
isuscope brief "${run}" >"${brief}" 2>&1 || true
python3 scripts/bench-result.py report "${run}" --brief "${brief}" --output "${bench_log}"
exit "${code}"
