#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
language=${1:-}
application_path=${2:-}

[[ "${language}" =~ ^[a-zA-Z0-9][a-zA-Z0-9._+-]*$ ]] || {
  echo "LANGUAGE must be a simple language name" >&2
  exit 2
}
if [[ -z "${application_path}" ]]; then
  application_path=webapp/${language}
fi
case "${application_path}" in
  webapp/*) ;;
  *) echo "APPLICATION_PATH must be under webapp/" >&2; exit 2 ;;
esac
[[ "${application_path}" != *".."* ]] || {
  echo "APPLICATION_PATH must not contain .." >&2
  exit 2
}
test -d "${repo_dir}/${application_path}" || {
  echo "application path does not exist after import: ${application_path}" >&2
  exit 1
}

mkdir -p "${repo_dir}/.local"
temporary=${repo_dir}/.local/application.env.$$
printf 'APPLICATION_LANGUAGE=%s\nAPPLICATION_PATH=%s\n' \
  "${language}" "${application_path}" >"${temporary}"
install -m 0644 "${temporary}" "${repo_dir}/config/application.env"
find "${temporary}" -delete
echo "application fixed: ${language} (${application_path})"
