#!/usr/bin/env bash
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "${script_dir}/sync-lib.sh"
if [[ "${ISUSCOPE_LOCK_HELD:-}" != 1 ]]; then
  exec isuscope lock --path "${script_dir}/../.local/operation.lock" -- "$0" "$@"
fi
sync_validate
source "${script_dir}/parallel-lib.sh"
parallel_init "${IMPORT_MAX_PARALLEL_NODES:-5}"
mkdir -p "${sync_repo_dir}/.local"
# Any new import supersedes the recorded distributed state; a successful one writes it again.
state_file=${sync_repo_dir}/.local/imported-state.json
rm -f -- "${state_file}"
workspace=$(mktemp -d "${sync_repo_dir}/.local/import.XXXXXX")
backup_root=${workspace}/backup
mkdir -p "${workspace}/digests" "${workspace}/staged" "${workspace}/reuse" "${backup_root}"
# Reject overlapping paths, including equivalent dotted spellings.
python3 -c '
import json, pathlib, sys
items=json.load(open(sys.argv[1]))["items"]
paths=[pathlib.Path(sys.argv[2],i["local"]).resolve() for i in items]
root=pathlib.Path(sys.argv[2]).resolve()
for n,p in enumerate(paths):
    if not p.is_relative_to(root) or p == root: raise SystemExit("unsafe import path")
    for q in paths[n+1:]:
        if p == q or p in q.parents or q in p.parents: raise SystemExit("overlapping import paths")
' "${sync_manifest}" "${sync_repo_dir}"
installed=()
backed_up=()
complete=false
cleanup() {
  local name path
  if [[ "${complete}" != true ]]; then
    for name in ${installed[@]+"${installed[@]}"}; do
      path=$(jq -r --arg name "${name}" '.items[] | select(.name==$name) | .local' "${sync_manifest}")
      mv -- "${sync_repo_dir}/${path}" "${workspace}/staged/${name}"
    done
    for name in ${backed_up[@]+"${backed_up[@]}"}; do
      path=$(jq -r --arg name "${name}" '.items[] | select(.name==$name) | .local' "${sync_manifest}")
      mv -- "${backup_root}/${name}" "${sync_repo_dir}/${path}"
    done
  fi
  echo "import diagnostics and recoverable backups: ${workspace}" >&2
}
trap cleanup EXIT

# Item excludes (sync.json "exclude") leave build outputs such as Rust target/ out of the
# digest on both sides and out of the transfer, so they are neither compared nor copied.
exclude_args() {
  local mode=$1 path
  for path in $2; do
    case "${mode}" in
      digest) printf " --exclude '%s'" "${path}" ;;
      tar) printf " --exclude='./%s'" "${path}" ;;
    esac
  done
}
digest_node() {
  local node=$1 name remote excludes command="set -eu # ISUCON_DIGEST_BATCH" expected=0
  while IFS=$'\t' read -r name remote excludes; do
    command+=$'\n'
    command+="printf '${name}\\t'; sudo python3 /usr/local/lib/isuscope/tree-digest.py '${remote}'$(exclude_args digest "${excludes}")"
    expected=$((expected+1))
  done < <(jq -r --arg node "${node}" --slurpfile inv "${sync_inventory}" '
    .items[] | select($inv[0].all.children[.node_group].hosts | has($node)) |
    [.name,.remote,((.exclude // []) | join(" "))] | @tsv' "${sync_manifest}")
  "${script_dir}/ssh-node.sh" "${node}" "${command}" </dev/null >"${workspace}/digests/${node}"
  test "$(wc -l <"${workspace}/digests/${node}" | tr -d ' ')" -eq "${expected}"
}
while IFS= read -r node; do
  parallel_start digest_node "${node}"
done < <(jq -r --slurpfile inv "${sync_inventory}" '
  [.items[].node_group] | unique[] | . as $group |
  $inv[0].all.children[$group].hosts | keys[]' "${sync_manifest}" | sort -u)
parallel_wait

comparison_file=${sync_repo_dir}/.local/import-comparison.tsv
printf 'item\tnode\tdigest\tsource\n' >"${comparison_file}"
divergent=0
while IFS=$'\t' read -r name group source; do
  previous=
  source_seen=false
  while IFS= read -r node; do
    digest=$(awk -F '\t' -v name="${name}" '$1==name{print $2}' "${workspace}/digests/${node}")
    [[ "${digest}" =~ ^[0-9a-f]{64}$ ]] || { echo "invalid digest: ${name} on ${node}" >&2; exit 1; }
    if [[ -n "${previous}" && "${previous}" != "${digest}" ]]; then divergent=1; fi
    previous=${digest}
    is_source=false
    [[ "${node}" != "${source}" ]] || { is_source=true; source_seen=true; }
    printf '%s\t%s\t%s\t%s\n' "${name}" "${node}" "${digest}" "${is_source}" >>"${comparison_file}"
  done < <(sync_group_nodes "${group}")
  [[ "${source_seen}" == true ]] || { echo "source node ${source} is not in ${group} for ${name}" >&2; exit 1; }
done < <(jq -r '.source_node as $s | .items[] | [.name,.node_group,(.source_node//$s)] | @tsv' "${sync_manifest}")
column -t -s $'\t' "${comparison_file}" 2>/dev/null || cat "${comparison_file}"
if [[ "${divergent}" != 0 && "${IMPORT_ALLOW_DIVERGENT:-false}" != true ]]; then
  echo "remote sync targets differ; review ${comparison_file} before IMPORT_ALLOW_DIVERGENT=true" >&2
  exit 1
fi

stage_node() {
  local source=$1 name type path remote excludes exclude expected digest staged parent base
  local -a digest_excludes
  while IFS=$'\t' read -r name type path remote excludes; do
    digest_excludes=()
    for exclude in ${excludes}; do digest_excludes+=(--exclude "${exclude}"); done
    expected=$(awk -F '\t' -v name="${name}" '$1==name{print $2}' "${workspace}/digests/${source}")
    [[ "${expected}" =~ ^[0-9a-f]{64}$ ]] || { echo "missing source digest: ${name} on ${source}" >&2; return 1; }
    digest=
    if [[ -e "${sync_repo_dir}/${path}" || -L "${sync_repo_dir}/${path}" ]]; then
      digest=$(python3 "${script_dir}/tree-digest.py" "${sync_repo_dir}/${path}" ${digest_excludes[@]+"${digest_excludes[@]}"})
    fi
    if [[ "${digest}" == "${expected}" ]]; then
      touch "${workspace}/reuse/${name}"
      echo "reusing unchanged local item: ${name}"
      continue
    fi
    staged=${workspace}/staged/${name}
    mkdir -p "${staged}"
    echo "importing ${name} from ${source}:${remote}"
    if [[ "${type}" == directory ]]; then
      "${script_dir}/ssh-node.sh" "${source}" "sudo tar -C '${remote}'$(exclude_args tar "${excludes}") -cf - ." </dev/null | tar -C "${staged}" -xpf -
    else
      parent=$(dirname -- "${remote}"); base=$(basename -- "${remote}")
      "${script_dir}/ssh-node.sh" "${source}" "sudo tar -C '${parent}' -cf - '${base}'" </dev/null | tar -C "${staged}" -xpf -
      mv "${staged}/${base}" "${staged}/item"
      staged=${staged}/item
    fi
    digest=$(python3 "${script_dir}/tree-digest.py" "${staged}" ${digest_excludes[@]+"${digest_excludes[@]}"})
    test "${digest}" == "${expected}" || { echo "digest mismatch after import: ${name}" >&2; return 1; }
  done < <(jq -r --arg source "${source}" '.source_node as $s | .items[] |
    select((.source_node//$s)==$source) | [.name,.type,.local,.remote,((.exclude // []) | join(" "))] | @tsv' "${sync_manifest}")
}
parallel_init "${IMPORT_MAX_PARALLEL_NODES:-5}"
while IFS= read -r source; do parallel_start stage_node "${source}"; done < <(
  jq -r '.source_node as $s | [.items[] | (.source_node//$s)] | unique[]' "${sync_manifest}")
parallel_wait
# Switch only after every staged item has passed validation.
while IFS=$'\t' read -r name type path; do
  [[ ! -f "${workspace}/reuse/${name}" ]] || continue
  mkdir -p "$(dirname -- "${sync_repo_dir}/${path}")"
  if [[ -e "${sync_repo_dir}/${path}" || -L "${sync_repo_dir}/${path}" ]]; then
    mv -- "${sync_repo_dir}/${path}" "${backup_root}/${name}"
    backed_up+=("${name}")
  fi
  staged=${workspace}/staged/${name}
  [[ "${type}" == directory ]] || staged+=/item
  mv -- "${staged}" "${sync_repo_dir}/${path}"
  installed+=("${name}")
done < <(jq -r '.items[] | [.name,.type,.local] | @tsv' "${sync_manifest}")
complete=true
# Until the first deploy the servers run exactly what was imported (the distributed build).
# make first-bench benchmarks that state when HEAD still has these digests, so the first
# run does not wait for the local build and deploy. Divergent nodes have no single state.
if [[ "${divergent}" == 0 ]]; then
  python3 - "${sync_manifest}" "${workspace}/digests" "${state_file}" <<'PY'
import json, sys, time
from pathlib import Path
manifest = json.loads(Path(sys.argv[1]).read_text())
digests = Path(sys.argv[2])
items = {}
for item in manifest['items']:
    source = item.get('source_node', manifest.get('source_node'))
    for line in (digests / source).read_text().splitlines():
        name, _, digest = line.partition('\t')
        if name == item['name']:
            items[name] = {'local': item['local'], 'digest': digest.strip(), 'exclude': item.get('exclude', [])}
temp = Path(sys.argv[3] + '.tmp')
temp.write_text(json.dumps({'imported_at': time.time(), 'items': items}, indent=2) + '\n')
temp.replace(sys.argv[3])
PY
fi
echo "import complete; all local digests match their selected source nodes"
