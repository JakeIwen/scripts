#!/bin/bash
# gracefully stop a running backup/restore before drives get unmounted.
# called by umount_disks.sh (including the ignition-on nodisk path); no-op when idle.
. /home/pi/scripts/backup/backup_conf.sh

[ -f "$JOB_LOCK" ] || exit 0
flock -n "$JOB_LOCK" true 2>/dev/null && exit 0  # lock free -> nothing running

pid=$(tr -cd 0-9 < "$JOB_LOCK")
if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
  echo "asking backup/restore (pid $pid) to stop before unmount"
  kill -TERM "$pid"
fi
flock -w 60 "$JOB_LOCK" true || echo "WARNING: backup/restore did not release its lock within 60s"
