#!/usr/bin/env bash
set -euo pipefail

# Starts one role's Claude Code session where it works, named after the role, so the other sessions
# reach it with SendMessage(to: "<role>") and nobody has to relay messages by hand (eleventh's
# sessions started unnamed, as practice-14-eleventh-03 and the like, and the person relayed them).
# Usage: session.sh main|improve|portal|human [PROMPT]
#   main runs in the root repository and also starts the board; the others in the worktree
#   <repo>-<role> that preparation made (make worktree BRANCH=<role>). PROMPT defaults to /<role>
#   (none for human).
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
role=${1:-}
shift || true

case "${role}" in
  main|improve|portal|human) ;;
  *) echo "usage: make session ROLE=main|improve|portal|human [PROMPT=...]" >&2; exit 2 ;;
esac

# The root repository, even when this runs from a worktree.
root=$(cd -- "$(git -C "${repo_dir}" rev-parse --path-format=absolute --git-common-dir)/.." && pwd)
if [[ "${role}" == main ]]; then
  place=${root}
else
  place=$(dirname -- "${root}")/$(basename -- "${root}")-${role}
  test -d "${place}" || {
    echo "no worktree for ${role}: ${place}; make it first (make worktree BRANCH=${role} PURPOSE=\"...\")" >&2
    exit 1
  }
fi

if [[ $# -gt 0 ]]; then
  prompt=("$@")
elif [[ "${role}" == human ]]; then
  prompt=()
else
  prompt=("/${role}")
fi

if [[ "${role}" == main ]]; then
  # The board is for the person; it reads the root's runs and queue, so one per practice is enough.
  port=${BOARD_PORT:-8765}
  if lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "board: port ${port} is already in use (the board, or another program; BOARD_PORT=<port> to move it)"
  else
    mkdir -p "${root}/.local"
    (cd "${root}" && nohup python3 scripts/board/main.py --port "${port}" >"${root}/.local/board.log" 2>&1 &)
    echo "board: http://127.0.0.1:${port} (log .local/board.log)"
  fi
fi

cd "${place}"
exec claude --name "${role}" --settings config/claude/session.json ${prompt[@]+"${prompt[@]}"}
