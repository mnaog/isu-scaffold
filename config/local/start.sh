#!/bin/sh
set -eu
# Build outside the read-only source; vendor/ is used when the module ships one.
# LOCAL_RACE=1 (make local-up with it set) builds with the race detector, which needs cgo; make local-race
# then reads the reports from the app's log.
if [ "${LOCAL_RACE:-}" = 1 ]; then
  CGO_ENABLED=1 go build -race -o /tmp/local-app "${LOCAL_GO_PACKAGE:-.}"
  echo "local app built with -race" >&2
else
  go build -o /tmp/local-app "${LOCAL_GO_PACKAGE:-.}"
fi
exec /tmp/local-app
