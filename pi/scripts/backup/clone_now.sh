#!/bin/bash
# Run one configured incremental hotspare clone under the shared backup lock.
# This wrapper is intentionally not an initializer: it accepts only labels
# already present in CLONE_TARGETS and never accepts a block-device path.
set -u
. /home/pi/scripts/backup/backup_conf.sh

label=${1:-}
[ $# -eq 1 ] || { echo "usage: clone_now.sh <configured-label>" >&2; exit 2; }

configured=0
for entry in "${CLONE_TARGETS[@]}"; do
  [ "${entry%%:*}" = "$label" ] && configured=1
done
[ "$configured" = 1 ] || { echo "unknown hotspare target" >&2; exit 2; }

acquire_job_lock || { echo "another backup or restore is active" >&2; exit 3; }

child=
stop_clone() {
  if [ -n "$child" ]; then
    kill -TERM "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
  fi
  exit 143
}
trap stop_clone TERM INT

# clone_to_sd.sh performs the final label, block-device, size, live-root, and
# mounted-partition checks immediately before writing.
/usr/bin/timeout --signal=TERM --kill-after=60 6h \
  /home/pi/scripts/clone_to_sd.sh "$label" &
child=$!
wait "$child"
status=$?
child=
exit "$status"
