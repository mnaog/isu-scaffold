#!/bin/sh
# Claude Code and Codex hooks (.claude/settings.json, .codex/hooks.json): runs isu-hook, the tool
# guard and the benchmark hook built from tools/isu-hook (`make hook`), with the tool call's JSON on
# stdin.
#
#   scripts/hook.sh guard | pre | post
#
# A worktree uses the build of its root repository. While isu-hook is not built, or is older than its
# source, pre and guard refuse every call but `make hook` (the rules stay on), and post does nothing.
# ISU_HOOK_BIN names another build (tests). Every call runs this, so it starts no other program
# before isu-hook (each costs about 25 ms on macOS).
case $0 in
  */*) scripts=${0%/*} ;;
  *) scripts=. ;;
esac
case ${scripts} in
  /*) ;;
  *) scripts=${PWD}/${scripts} ;;
esac
repo=${scripts%/*}
mode=${1:-}
built=${repo}/.local/isu-hook/release/isu-hook
bin=${ISU_HOOK_BIN:-${built}}
stale=0
if [ -z "${ISU_HOOK_BIN:-}" ]; then
  if [ -x "${built}" ]; then
    for source in "${repo}"/tools/isu-hook/Cargo.toml "${repo}"/tools/isu-hook/Cargo.lock "${repo}"/tools/isu-hook/src/*.rs; do
      [ "${source}" -nt "${built}" ] && stale=1
    done
  elif [ -f "${repo}/.git" ] && { read -r gitdir <"${repo}/.git" && read -r commondir <"${gitdir#gitdir: }/commondir"; } 2>/dev/null; then
    # A worktree: .git names its git directory, and commondir the root's.
    gitdir=${gitdir#gitdir: }
    case ${gitdir} in /*) ;; *) gitdir=${repo}/${gitdir} ;; esac
    case ${commondir} in /*) ;; *) commondir=${gitdir}/${commondir} ;; esac
    bin=${commondir}/../.local/isu-hook/release/isu-hook
  elif common=$(git -C "${repo}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null); then
    bin=${common%/*}/.local/isu-hook/release/isu-hook
  fi
fi
export TOOL_GUARD_ROOT="${TOOL_GUARD_ROOT:-${repo}}" ISU_HOOK_SCRIPTS="${scripts}"
if [ -x "${bin}" ] && [ "${stale}" = 0 ]; then
  exec "${bin}" "${mode}"
fi
[ "${mode}" = post ] && exit 0
if grep -Eq '"command" *: *"(cd [^"&;|]+ && )?make( -s)?( -C [^"&;|]+)? hook"' 2>/dev/null; then
  exit 0
fi
reason='isu-hook（tools/isu-hook）が未buildか、sourceより古い。先にrepoのrootで`make hook`を実行する（禁止操作のhookをbuildする。ほかの操作はbuildまで止まる）。'
printf '{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "%s"}}\n' "${reason}"
