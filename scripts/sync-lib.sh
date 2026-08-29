#!/usr/bin/env bash

sync_script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
sync_repo_dir=$(cd -- "${sync_script_dir}/.." && pwd)
sync_manifest=${SYNC_MANIFEST:-${sync_repo_dir}/config/sync.json}
sync_inventory=${ANSIBLE_INVENTORY:-${sync_repo_dir}/.local/ansible-inventory.json}
case "${sync_manifest}" in /*) ;; *) sync_manifest=${sync_repo_dir}/${sync_manifest} ;; esac
case "${sync_inventory}" in /*) ;; *) sync_inventory=${sync_repo_dir}/${sync_inventory} ;; esac

sync_validate() {
  command -v jq >/dev/null
  test -f "${sync_manifest}" || { echo "missing sync manifest: ${sync_manifest}" >&2; return 1; }
  test -f "${sync_inventory}" || { echo "run make discover first" >&2; return 1; }
  jq -e '
    .items as $items |
    (.source_node | type == "string" and length > 0) and
    ((.minimum_free_mb_after_deploy // 1024) | type == "number" and floor == . and . >= 0) and
    (.pre_deploy_command | type == "string") and
    ($items | type == "array" and length > 0 and (map(.name) | length == (unique | length))) and
    all($items[];
      (.name | type == "string" and test("^[A-Za-z0-9_.-]+$")) and
      (.type == "file" or .type == "directory") and
      (.node_group | type == "string" and test("^[A-Za-z0-9_.-]+$")) and
      (.local | type == "string" and test("^[A-Za-z0-9_./-]+$") and
        (startswith("/") | not) and (split("/") | index("..") == null)) and
      (.remote | type == "string" and test("^/[A-Za-z0-9_./-]+$") and
        (split("/") | index("..") == null) and
        . != "/" and . != "/etc" and . != "/home" and . != "/opt" and
        . != "/root" and . != "/srv" and . != "/usr" and . != "/var") and
      (.owner | type == "string" and test("^[A-Za-z0-9_.-]+$")) and
      (.owner_group | type == "string" and test("^[A-Za-z0-9_.-]+$"))) and
    ((.build_commands // []) | type == "array" and all(.[];
      (type == "object") and
      (.name | type == "string" and test("^[A-Za-z0-9_.-]+$")) and
      (.node_group | type == "string" and test("^[A-Za-z0-9_.-]+$")) and
      (.item | type == "string" and test("^[A-Za-z0-9_.-]+$")) and
      (.command | type == "string" and length > 0 and (test("[\\t\\r\\n]") | not)))) and
    (($items | map(.name)) as $item_names |
      all((.build_commands // [])[]; .item as $item | $item_names | index($item) != null)) and
    (.post_deploy_commands | type == "array" and all(.[];
      (type == "string" and length > 0) or
      (type == "object" and
        (.node_group | type == "string" and test("^[A-Za-z0-9_.-]+$")) and
        (.command | type == "string" and length > 0)))) and
    (.status_commands | type == "array" and all(.[];
      (type == "string" and length > 0) or
      (type == "object" and
        (.node_group | type == "string" and test("^[A-Za-z0-9_.-]+$")) and
        (.command | type == "string" and length > 0))))
  ' "${sync_manifest}" >/dev/null || {
    echo "config/sync.json is incomplete or invalid" >&2
    return 1
  }

  local source_node item_source item_group local_path
  source_node=$(jq -r '.source_node' "${sync_manifest}")
  sync_node_exists "${source_node}" || { echo "unknown source_node: ${source_node}" >&2; return 1; }
  while IFS=$'\t' read -r item_group local_path item_source; do
    jq -e --arg group "${item_group}" '.all.children[$group].hosts | length > 0' \
      "${sync_inventory}" >/dev/null || {
        echo "sync item references an empty or unknown node group: ${item_group}" >&2
        return 1
      }
    case "${local_path}" in
      webapp|webapp/*|config|config/*) ;;
      *) echo "sync local path must be under webapp/ or config/: ${local_path}" >&2; return 1 ;;
    esac
    [[ -n "${item_source}" ]] || item_source=${source_node}
    jq -e --arg group "${item_group}" --arg source "${item_source}" \
      '.all.children[$group].hosts | has($source)' "${sync_inventory}" >/dev/null || {
        echo "source_node ${item_source} is not in sync item group ${item_group}" >&2
        return 1
      }
  done < <(jq -r '.source_node as $default | .items[] | [.node_group, .local, (.source_node // $default)] | @tsv' "${sync_manifest}")

  local command_group
  while IFS= read -r command_group; do
    jq -e --arg group "${command_group}" '.all.children[$group].hosts | length > 0' \
      "${sync_inventory}" >/dev/null || {
        echo "sync command references an empty or unknown node group: ${command_group}" >&2
        return 1
      }
  done < <(jq -r '
    [(.build_commands // [])[], .post_deploy_commands[], .status_commands[]] | .[] |
    if type == "string" then "application" else .node_group end
  ' "${sync_manifest}" | sort -u)

  local build_name build_group build_item item_group node
  while IFS=$'\t' read -r build_name build_group build_item; do
    item_group=$(jq -r --arg item "${build_item}" '.items[] | select(.name == $item) | .node_group' \
      "${sync_manifest}")
    while IFS= read -r node; do
      jq -e --arg group "${item_group}" --arg node "${node}" \
        '.all.children[$group].hosts | has($node)' "${sync_inventory}" >/dev/null || {
        echo "build command ${build_name} targets ${node}, but item ${build_item} is not staged there" >&2
        return 1
      }
    done < <(sync_group_nodes "${build_group}")
  done < <(jq -r '(.build_commands // [])[] | [.name, .node_group, .item] | @tsv' "${sync_manifest}")
}

sync_node_exists() {
  jq -e --arg name "$1" '[.all.children[].hosts | keys[]] | index($name) != null' \
    "${sync_inventory}" >/dev/null
}

sync_group_nodes() {
  jq -r --arg group "$1" '.all.children[$group].hosts | keys[]' "${sync_inventory}"
}

sync_commands() {
  jq -r --arg key "$1" '
    .[$key][] |
    if type == "string" then ["application", .] else [.node_group, .command] end |
    @tsv
  ' "${sync_manifest}"
}

sync_build_commands() {
  jq -r '(.build_commands // [])[] | [.name, .node_group, .item, .command] | @tsv' \
    "${sync_manifest}"
}

sync_validate_release() {
  case "$1" in ''|*[!A-Za-z0-9._-]*) echo "invalid release: $1" >&2; return 1 ;; esac
}
