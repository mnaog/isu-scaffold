#!/bin/sh
set -eu

escape_json() {
  sed 's/\\/\\\\/g; s/"/\\"/g; s/[[:cntrl:]]/ /g'
}

emit() {
  name=$1
  value=$(printf '%s' "$2" | escape_json)
  printf '{"type":"fingerprint","name":"%s","value":"%s"}\n' "$name" "$value"
}

hash_file() {
  if [ -f "$1" ]; then
    sha256sum "$1" | awk '{print $1}'
  else
    printf 'missing'
  fi
}

path_key() {
  printf '%s' "$1" | sha256sum | awk '{print substr($1, 1, 16)}'
}

emit kernel "$(uname -srmo)"
# /etc/os-release is the standard source on the supported Linux hosts.
# shellcheck disable=SC1091
emit os.release "$(. /etc/os-release; printf '%s %s' "${ID:-unknown}" "${VERSION_ID:-unknown}")"
targets_file=/usr/local/lib/isuscope/fingerprint-paths
if [ -r "${targets_file}" ]; then
  while IFS= read -r path; do
    case "${path}" in ''|'#'*) continue ;; esac
    emit "file.$(path_key "${path}").sha256" "${path}:$(hash_file "${path}")"
  done <"${targets_file}"
fi
if command -v nginx >/dev/null 2>&1; then
  emit nginx.version "$(nginx -v 2>&1)"
  emit nginx.config.sha256 "$(nginx -T 2>&1 | sha256sum | awk '{print $1}')"
else
  emit nginx.version unavailable
fi
if command -v mysql >/dev/null 2>&1; then
  emit mysql.version "$(mysql --version)"
else
  emit mysql.version unavailable
fi
if command -v systemctl >/dev/null 2>&1; then
  emit systemd.running.sha256 "$(systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null | sha256sum | awk '{print $1}')"
fi
