#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi

language=${1:-}
application_path=${2:-webapp/${language}}
application_remote=${CODE_REMOTE_PATH:-/home/isucon/webapp/${language}}
schema_path=${CODE_SCHEMA_PATH:-webapp/sql}
schema_remote=${CODE_SCHEMA_REMOTE_PATH-/home/isucon/webapp/sql}
inventory=${ANSIBLE_INVENTORY:-${repo_dir}/.local/ansible-inventory.json}

[[ "${language}" =~ ^[a-zA-Z0-9][a-zA-Z0-9._+-]*$ ]] || {
  echo "LANGUAGE must be a simple language name" >&2
  exit 2
}
[[ "${application_path}" =~ ^webapp/[a-zA-Z0-9_./+-]+$ && "${application_path}" != *".."* ]] || {
  echo "APPLICATION_PATH must be a safe path under webapp/" >&2
  exit 2
}
[[ "${schema_path}" =~ ^webapp/[a-zA-Z0-9_./+-]+$ && "${schema_path}" != *".."* ]] || {
  echo "CODE_SCHEMA_PATH must be a safe path under webapp/" >&2
  exit 2
}
[[ "${application_remote}" =~ ^/[a-zA-Z0-9_./+-]+$ ]] || {
  echo "CODE_REMOTE_PATH contains unsupported characters" >&2
  exit 2
}
[[ "${application_remote}" != *".."* && "${application_remote}" != / ]] || {
  echo "unsafe CODE_REMOTE_PATH" >&2
  exit 2
}
if [[ -n "${schema_remote}" ]]; then
  [[ "${schema_remote}" =~ ^/[a-zA-Z0-9_./+-]+$ ]] || {
    echo "CODE_SCHEMA_REMOTE_PATH contains unsupported characters" >&2
    exit 2
  }
  [[ "${schema_remote}" != *".."* && "${schema_remote}" != / ]] || {
    echo "unsafe CODE_SCHEMA_REMOTE_PATH" >&2
    exit 2
  }
fi

python3 -c '
from pathlib import Path
import sys
root=Path(sys.argv[1]).resolve()
a,b=(root/p for p in sys.argv[2:])
for p in (a,b):
    if p.resolve()!=p or not p.resolve().is_relative_to(root): raise SystemExit("symlink or noncanonical import target")
if a==b or a in b.parents or b in a.parents: raise SystemExit("application and schema paths overlap")
' "${repo_dir}" "${application_path}" "${schema_path}"
command -v jq >/dev/null
test -f "${inventory}" || { echo "run make discover first" >&2; exit 1; }
source_node=${CODE_SOURCE_NODE:-$(jq -r '.all.children.application.hosts | keys[0] // empty' "${inventory}")}
test -n "${source_node}" || { echo "no application node found; set CODE_SOURCE_NODE" >&2; exit 1; }

pending=$(git -C "${repo_dir}" status --porcelain --untracked-files=all -- \
  config/application.env "${application_path}" "${schema_path}")
test -z "${pending}" || {
  echo "quick code import refuses to overwrite local changes:" >&2
  printf '%s\n' "${pending}" >&2
  exit 1
}

"${script_dir}/ssh-node.sh" "${source_node}" "sudo test -d '${application_remote}'" || {
  echo "application directory not found on ${source_node}: ${application_remote}" >&2
  exit 1
}

mkdir -p "${repo_dir}/.local"
staging_root=$(mktemp -d "${repo_dir}/.local/code-import.XXXXXX")
cleanup() { find "${staging_root}" -depth -delete 2>/dev/null || true; }
trap cleanup EXIT
mkdir -p "${staging_root}/application"
"${script_dir}/ssh-node.sh" "${source_node}" \
  "sudo tar -C '${application_remote}' --exclude='./target' --exclude='./node_modules' --exclude='./.git' -cf - ." |
  tar -C "${staging_root}/application" -xpf -

schema_imported=false
if [[ -n "${schema_remote}" ]]; then
  mkdir -p "${staging_root}/schema"
  schema_command=$(python3 -c 'import pathlib,shlex,sys; print(shlex.join(["sudo","-n","python3","-c",pathlib.Path(sys.argv[1]).read_text(),*sys.argv[2:]]))' \
    "${script_dir}/schema-archive.py" "${schema_remote}" "${CODE_SCHEMA_MAX_FILE_BYTES:-1048576}" "${CODE_SCHEMA_MAX_TOTAL_BYTES:-16777216}")
  "${script_dir}/ssh-node.sh" "${source_node}" "${schema_command}" 2>"${repo_dir}/.local/code-schema-manifest.log" |
    tar -C "${staging_root}/schema" -xpf -
  schema_imported=true
fi

backup_root=${repo_dir}/.local/code-import-backup/$(date +%Y%m%d-%H%M%S)-$$
install_staged_directory() {
  local staged=$1 local_path=$2 absolute=${repo_dir}/$2
  mkdir -p "$(dirname -- "${absolute}")"
  if [[ -e "${absolute}" ]]; then
    mkdir -p "${backup_root}/$(dirname -- "${local_path}")"
    mv -- "${absolute}" "${backup_root}/${local_path}"
  fi
  mv -- "${staged}" "${absolute}"
}
install_staged_directory "${staging_root}/application" "${application_path}"
if [[ "${schema_imported}" == true ]]; then
  install_staged_directory "${staging_root}/schema" "${schema_path}"
else
  echo "schema directory was not found; continuing without it: ${schema_remote:-disabled}"
fi
"${script_dir}/set-application-language.sh" "${language}" "${application_path}"

echo "quick code import complete from ${source_node}; schema omissions: .local/code-schema-manifest.log"
echo "review and commit config/application.env, ${application_path}, and ${schema_path} if present"
echo "then run: make kickoff-code-ready"
