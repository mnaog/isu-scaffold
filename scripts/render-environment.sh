#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
local_dir=${repo_dir}/.local
environment_file=${ISUCON_ENV_FILE:-${local_dir}/environment.env}
nodes_file=${local_dir}/discovered-nodes.json
inventory_file=${local_dir}/ansible-inventory.json
node_toml_file=${local_dir}/isuscope-nodes.toml
isuscope_template=${repo_dir}/.isuscope/config.template.toml
isuscope_config=${repo_dir}/.isuscope/config.toml

command -v jq >/dev/null
test -f "${environment_file}"
test -f "${nodes_file}"
test -f "${isuscope_template}"
# shellcheck disable=SC1090
source "${environment_file}"

ssh_user=${SSH_USER:?SSH_USER is required}
identity_file=${SSH_IDENTITY_FILE:?SSH_IDENTITY_FILE is required}
known_hosts_file=${SSH_KNOWN_HOSTS_FILE:-.local/known-hosts}
nginx_access_log=${ISUSCOPE_NGINX_ACCESS_LOG:-/var/log/nginx/access.log}
mysql_slow_log=${ISUSCOPE_MYSQL_SLOW_LOG:-/var/log/mysql/mysql-slow.log}
service_units=${ISUSCOPE_SERVICE_UNITS:-}
isuscope_overrides=${local_dir}/isuscope-overrides.json
if [[ -f "${isuscope_overrides}" ]]; then
  nginx_access_log=$(jq -r '.ISUSCOPE_NGINX_ACCESS_LOG // $default' --arg default "${nginx_access_log}" "${isuscope_overrides}")
  mysql_slow_log=$(jq -r '.ISUSCOPE_MYSQL_SLOW_LOG // $default' --arg default "${mysql_slow_log}" "${isuscope_overrides}")
  service_units=$(jq -r '.ISUSCOPE_SERVICE_UNITS // $default' --arg default "${service_units}" "${isuscope_overrides}")
fi
for log_path in "${nginx_access_log}" "${mysql_slow_log}"; do
  [[ "${log_path}" =~ ^/[A-Za-z0-9_./-]+$ ]] && [[ "${log_path}" != *'/../'* ]] || {
    echo "invalid isuscope log path: ${log_path}" >&2
    exit 1
  }
done
service_units_toml=
for unit in ${service_units}; do
  [[ "${unit}" =~ ^[A-Za-z0-9._@-]+$ ]] || {
    echo "invalid isuscope service unit: ${unit}" >&2
    exit 1
  }
  encoded=$(jq -Rn --arg value "${unit}" '$value')
  if [[ -n "${service_units_toml}" ]]; then
    service_units_toml+=", "
  fi
  service_units_toml+="${encoded}"
done

jq -e '
  type == "array" and length > 0 and
  all(.[];
    (.name | type == "string" and length > 0 and test("^[A-Za-z0-9_.-]+$")) and
    (.host | type == "string" and length > 0) and
    (.group == "application" or .group == "benchmark") and
    (.user | type == "string" and length > 0) and
    (.roles | type == "array" and all(.[]; type == "string" and test("^[A-Za-z0-9_]+$")))) and
  ([.[].name] | length == (unique | length))
' "${nodes_file}" >/dev/null || {
  echo "invalid discovered nodes: require unique name, host, user, roles and application/benchmark group" >&2
  exit 1
}

application_count=$(jq '[.[] | select(.group == "application")] | length' "${nodes_file}")
test "${application_count}" -gt 0 || { echo "at least one application node is required" >&2; exit 1; }

inventory_tmp=$(mktemp "${inventory_file}.XXXXXX")
nodes_tmp=$(mktemp "${node_toml_file}.XXXXXX")
config_tmp=$(mktemp "${isuscope_config}.XXXXXX")
cleanup() { rm -f -- "${inventory_tmp}" "${nodes_tmp}" "${config_tmp}"; }
trap cleanup EXIT

jq \
  --arg default_user "${ssh_user}" \
  --arg identity_file "${identity_file}" \
  --arg known_hosts_file "${known_hosts_file}" '
  . as $nodes |
  def group_hosts($group):
    reduce ($nodes[] | select(.group == $group)) as $node ({};
      .[$node.name] = ({
        ansible_host: $node.host,
        ansible_user: $node.user,
        isuscope_roles: $node.roles,
        node_provider: $node.provider,
        node_state: $node.state
      }
      + (if $node.instance_id then {aws_instance_id: $node.instance_id} else {} end)
      + (if $node.availability_zone then {aws_availability_zone: $node.availability_zone} else {} end)));
  def role_children:
    reduce ([$nodes[] | select(.group == "application") | .roles[]] | unique[]) as $role ({};
      .["role_" + $role] = {
        hosts: (reduce ($nodes[] | select(.group == "application" and (.roles | index($role) != null))) as $node ({};
          .[$node.name] = ({
            ansible_host: $node.host,
            ansible_user: $node.user,
            isuscope_roles: $node.roles,
            node_provider: $node.provider,
            node_state: $node.state
          }
          + (if $node.instance_id then {aws_instance_id: $node.instance_id} else {} end)
          + (if $node.availability_zone then {aws_availability_zone: $node.availability_zone} else {} end))))
      });
  {
    all: {
      vars: {
        ansible_user: $default_user,
        ansible_ssh_private_key_file: $identity_file,
        ansible_ssh_common_args: ("-o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=" + $known_hosts_file)
      },
      children: ({
        application: {hosts: group_hosts("application")},
        benchmark: {hosts: group_hosts("benchmark")}
      } + role_children)
    }
  }
' "${nodes_file}" >"${inventory_tmp}"

jq -r '
  .[] | select(.group == "application") |
  "[[nodes]]\nname = " + (.name | @json) +
  "\nhost = " + (.host | @json) +
  "\nroles = " + (.roles | @json) +
  "\nuser = " + (.user | @json) + "\n"
' "${nodes_file}" >"${nodes_tmp}"

{
  sed \
    -e "s|@@NGINX_ACCESS_LOG@@|${nginx_access_log}|g" \
    -e "s|@@MYSQL_SLOW_LOG@@|${mysql_slow_log}|g" \
    -e "s|@@SERVICE_UNITS@@|${service_units_toml}|g" \
    "${isuscope_template}"
  printf '\n[ssh]\nuser = %s\nidentity_file = %s\nknown_hosts_file = %s\nconnect_timeout_seconds = 5\n\n' \
    "$(jq -Rn --arg value "${ssh_user}" '$value')" \
    "$(jq -Rn --arg value "${identity_file}" '$value')" \
    "$(jq -Rn --arg value "${known_hosts_file}" '$value')"
  cat "${nodes_tmp}"
} >"${config_tmp}"

mv -- "${inventory_tmp}" "${inventory_file}"
mv -- "${nodes_tmp}" "${node_toml_file}"
mv -- "${config_tmp}" "${isuscope_config}"
jq '{generated_at: (now | todateiso8601), nodes: .}' "${nodes_file}" >"${local_dir}/nodes.snapshot.json"
trap - EXIT

printf 'discovered %s nodes (%s application)\n' "$(jq length "${nodes_file}")" "${application_count}"
printf 'inventory: %s\n' "${inventory_file}"
printf 'isuscope config: %s\n' "${isuscope_config}"
