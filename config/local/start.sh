#!/bin/sh
set -eu
if [ "${LOCAL_USE_VENDOR:-0}" = 1 ]; then
  exec cargo --config 'source.crates-io.replace-with="local-vendor"' \
    --config 'source.local-vendor.directory="/vendor"' run --offline --release --locked
fi
exec cargo run --release --locked
