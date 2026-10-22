#!/bin/sh
set -eu
# Build outside the read-only source; vendor/ is used when the module ships one.
go build -o /tmp/local-app "${LOCAL_GO_PACKAGE:-.}"
exec /tmp/local-app
