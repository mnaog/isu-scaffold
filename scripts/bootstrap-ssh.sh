#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUCON_INTERNAL_OPERATION_LOCK_HELD:-false}" != true ]]; then
  exec "${script_dir}/with-operation-lock.sh" "$0" "$@"
fi
local_dir=${repo_dir}/.local
environment_file=${ISUCON_ENV_FILE:-${local_dir}/environment.env}
inventory_path=${local_dir}/ansible-inventory.json

command -v jq >/dev/null
command -v ssh >/dev/null
test -f "${environment_file}"
test -f "${inventory_path}" || { echo "run make discover first" >&2; exit 1; }

# shellcheck disable=SC1090
source "${environment_file}"

method=${SSH_BOOTSTRAP_METHOD:-existing}
identity_file=${SSH_IDENTITY_FILE:?SSH_IDENTITY_FILE is required}
known_hosts_file=${SSH_KNOWN_HOSTS_FILE:-.local/known-hosts}
case "${identity_file}" in /*) ;; *) identity_file=${repo_dir}/${identity_file} ;; esac
case "${known_hosts_file}" in /*) ;; *) known_hosts_file=${repo_dir}/${known_hosts_file} ;; esac

if [[ "${method}" == eic ]]; then
  command -v aws >/dev/null
  command -v ssh-keygen >/dev/null
  aws_profile=${AWS_PROFILE:?AWS_PROFILE is required for EIC}
  aws_region=${AWS_REGION:?AWS_REGION is required for EIC}
  if [[ ! -f "${identity_file}" ]]; then
    mkdir -p "$(dirname -- "${identity_file}")"
    umask 077
    ssh-keygen -q -t ed25519 -N '' -f "${identity_file}"
  fi
  test -f "${identity_file}.pub"
  public_key_base64=$(base64 <"${identity_file}.pub" | tr -d '\n')
elif [[ "${method}" == existing ]]; then
  test -f "${identity_file}" || { echo "missing existing SSH identity: ${identity_file}" >&2; exit 1; }
else
  echo "SSH_BOOTSTRAP_METHOD must be eic or existing" >&2
  exit 2
fi

mkdir -p "$(dirname -- "${known_hosts_file}")"
while IFS=$'\t' read -r host_alias endpoint ssh_user instance_id availability_zone instance_state <&3; do
  if [[ -n "${instance_state}" && "${instance_state}" != running ]]; then
    echo "${host_alias} is ${instance_state}; start it and run make discover again" >&2
    exit 1
  fi
  printf 'checking SSH: %s (%s)\n' "${host_alias}" "${endpoint}"
  if [[ "${method}" == eic ]]; then
    test -n "${instance_id}" && test -n "${availability_zone}" || {
      echo "EIC requires AWS instance metadata for ${host_alias}" >&2
      exit 1
    }
    aws ec2-instance-connect send-ssh-public-key \
      --profile "${aws_profile}" \
      --region "${aws_region}" \
      --instance-id "${instance_id}" \
      --availability-zone "${availability_zone}" \
      --instance-os-user "${ssh_user}" \
      --ssh-public-key "file://${identity_file}.pub" \
      --query Success \
      --output text >/dev/null
  fi

  if [[ "${method}" == eic ]]; then
    ssh \
      -n \
      -i "${identity_file}" \
      -o BatchMode=yes \
      -o ConnectTimeout=5 \
      -o StrictHostKeyChecking=accept-new \
      -o "UserKnownHostsFile=${known_hosts_file}" \
      "${ssh_user}@${endpoint}" \
      "set -eu; key=\$(printf '%s' '${public_key_base64}' | base64 -d); install -d -m 700 ~/.ssh; touch ~/.ssh/authorized_keys; chmod 600 ~/.ssh/authorized_keys; grep -qxF \"\$key\" ~/.ssh/authorized_keys || printf '%s\\n' \"\$key\" >> ~/.ssh/authorized_keys"
  else
    ssh \
      -n \
      -i "${identity_file}" \
      -o BatchMode=yes \
      -o ConnectTimeout=5 \
      -o StrictHostKeyChecking=accept-new \
      -o "UserKnownHostsFile=${known_hosts_file}" \
      "${ssh_user}@${endpoint}" true
  fi
done 3< <(jq -r '
  .all.children[]?.hosts | to_entries[] |
  [.key, .value.ansible_host, .value.ansible_user,
   (.value.aws_instance_id // ""), (.value.aws_availability_zone // ""),
   (.value.node_state // "")] | @tsv
' "${inventory_path}")

echo "SSH is available on all discovered nodes"
