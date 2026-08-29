#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "${script_dir}/.." && pwd)
local_dir=${repo_dir}/.local
environment_file=${ISUCON_ENV_FILE:-${local_dir}/environment.env}

command -v aws >/dev/null
command -v jq >/dev/null
test -f "${environment_file}"

# shellcheck disable=SC1090
source "${environment_file}"

aws_profile=${AWS_PROFILE:?AWS_PROFILE is required for aws-cloudformation discovery}
aws_region=${AWS_REGION:?AWS_REGION is required for aws-cloudformation discovery}
stack_name=${CLOUDFORMATION_STACK:?CLOUDFORMATION_STACK is required for aws-cloudformation discovery}
ssh_user=${SSH_USER:?SSH_USER is required}
app_name_regex=${DISCOVERY_APP_NAME_REGEX:-'(app|web)[0-9]*$'}
bench_name_regex=${DISCOVERY_BENCH_NAME_REGEX:-'(bench|benchmark)[0-9]*$'}

mkdir -p "${local_dir}"
raw_file=$(mktemp "${local_dir}/aws-instances.raw.XXXXXX")
output_file=$(mktemp "${local_dir}/discovered-nodes.XXXXXX")
cleanup() { rm -f -- "${raw_file}" "${output_file}"; }
trap cleanup EXIT

aws ec2 describe-instances \
  --profile "${aws_profile}" \
  --region "${aws_region}" \
  --filters "Name=tag:aws:cloudformation:stack-name,Values=${stack_name}" \
    'Name=instance-state-name,Values=pending,running,stopping,stopped' \
  --query 'Reservations[].Instances[]' \
  --output json >"${raw_file}"

jq \
  --arg app_regex "${app_name_regex}" \
  --arg bench_regex "${bench_name_regex}" \
  --arg ssh_user "${ssh_user}" '
  def tag($key): ([.Tags[]? | select(.Key == $key) | .Value][0] // "");
  def display_name:
    (tag("Name")) as $name | if $name == "" then .InstanceId else $name end;
  def alias:
    display_name | ascii_downcase | gsub("[^a-z0-9_.-]"; "-") | gsub("-+"; "-");
  map({
    name: alias,
    display_name: display_name,
    host: (.PublicIpAddress // .PrivateIpAddress // ""),
    group: (display_name as $name |
      if ($name | test($bench_regex; "i")) then "benchmark"
      elif ($name | test($app_regex; "i")) then "application"
      else "unclassified" end),
    roles: (display_name as $name |
      if ($name | test($bench_regex; "i")) then ["benchmark"] else ["app"] end),
    user: $ssh_user,
    provider: "aws-cloudformation",
    instance_id: .InstanceId,
    availability_zone: .Placement.AvailabilityZone,
    image_id: .ImageId,
    state: .State.Name,
    public_ip: (.PublicIpAddress // null),
    private_ip: (.PrivateIpAddress // null)
  }) | sort_by(.name)
' "${raw_file}" >"${output_file}"

unclassified=$(jq -r '.[] | select(.group == "unclassified") | .display_name' "${output_file}")
if [[ -n "${unclassified}" ]]; then
  echo "nodes matched neither application nor benchmark regex:" >&2
  printf '%s\n' "${unclassified}" | sed 's/^/  /' >&2
  exit 1
fi

mv -- "${output_file}" "${local_dir}/discovered-nodes.json"
rm -f -- "${raw_file}"
trap - EXIT
