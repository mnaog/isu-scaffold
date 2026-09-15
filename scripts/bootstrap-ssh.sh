#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi
local_dir=${repo_dir}/.local
environment_file=${ISUCON_ENV_FILE:-${local_dir}/environment.env}
inventory_path=${ANSIBLE_INVENTORY:-${local_dir}/ansible-inventory.json}
source "${script_dir}/parallel-lib.sh"
parallel_init "${SSH_MAX_PARALLEL_NODES:-5}"

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
connect_node() {
  local host_alias=$1 endpoint=$2 ssh_user=$3 instance_id=$4 availability_zone=$5 instance_state=$6
  if [[ "${instance_state}" != "-" && -n "${instance_state}" && "${instance_state}" != running ]]; then
    echo "${host_alias} is ${instance_state}; start it and run make discover again" >&2
    exit 1
  fi
  printf 'checking SSH: %s (%s)\n' "${host_alias}" "${endpoint}"
  if [[ "${method}" == eic ]]; then
    [[ "${instance_id}" != "-" && "${availability_zone}" != "-" ]] && test -n "${instance_id}" && test -n "${availability_zone}" || {
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
}
node_rows=$(jq -r --arg only "${SSH_ONLY_NODE:-}" '
  [.all.children[]?.hosts | to_entries[]] | unique_by(.key)[] |
  select($only == "" or .key == $only) |
  [.key, .value.ansible_host, .value.ansible_user,
   (.value.aws_instance_id // "-"), (.value.aws_availability_zone // "-"),
   (.value.node_state // "-")] | @tsv
' "${inventory_path}")
test -n "${node_rows}" || { echo "no matching SSH nodes" >&2; exit 2; }
while IFS=$'\t' read -r host_alias endpoint ssh_user instance_id availability_zone instance_state; do
  parallel_start connect_node "${host_alias}" "${endpoint}" "${ssh_user}" "${instance_id}" "${availability_zone}" "${instance_state}"
done <<<"${node_rows}"
parallel_wait

echo "SSH is available on selected nodes"
