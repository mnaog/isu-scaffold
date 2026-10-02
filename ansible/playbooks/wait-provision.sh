#!/usr/bin/env bash
set -euo pipefail
# SSH readiness does not imply that cloud-init has finished importing the DB.
cloud_status=0
if command -v cloud-init >/dev/null 2>&1; then
  cloud-init status --wait || cloud_status=$?
fi
# Practice recovery jobs run detached after cloud-final has been stopped.
# Match their command lines, not this shell or an unrelated participant process.
while ps -eo args= | awk '
  /^(\/usr\/bin\/)?python3? \/opt\/continue-practice-provision.py( |$)/ ||
  /^(\/usr\/bin\/)?bash \/opt\/finish-practice-app.sh( |$)/ { found=1 }
  END { exit !found }'; do
  sleep 2
done
if test -e /opt/continue-practice-provision.py || test -e /opt/finish-practice-app.sh; then
  test -f /var/lib/isucon-provision-complete || {
    echo 'practice provisioning did not complete; inspect its logs before bootstrap' >&2
    exit 1
  }
else
  exit "$cloud_status"
fi
