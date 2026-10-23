# Bundled measurement tools

The observability role copies these to every node instead of downloading them there
(`observability_tools_source: bundled`), so a node without GitHub access, without `git`, or
behind a rate limit still gets the same versions. The versions are pinned in
`ansible/roles/observability/defaults/main.yml`.

| File | Source | License |
| --- | --- | --- |
| `slp_<version>_linux_<arch>.tar.gz` | `https://github.com/tkuchiki/slp/releases` (`slp_linux_<arch>.tar.gz`, LICENSE inside) | MIT |

The archives are kept as released; only the version is added to their file names.
`SHA256SUMS` lists every file.

To update: change the version in the defaults, run
`./scripts/fetch-observability-tools.sh --update`, and commit this directory.
`./scripts/fetch-observability-tools.sh` without arguments checks the files against the pinned
versions and `SHA256SUMS`.
