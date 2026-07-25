#!/bin/bash
# gracefully stop a running backup/restore before drives get unmounted.
# called by umount_disks.sh (including the ignition-on nodisk path); no-op when idle.
set -u
. /home/pi/scripts/backup/backup_conf.sh

emergency=0
case "${1:-}" in
  "") ;;
  --emergency) emergency=1 ;;
  *)
    echo "usage: ${0##*/} [--emergency]" >&2
    exit 2
    ;;
esac

job_lock=${ABORT_BACKUP_JOB_LOCK:-$JOB_LOCK}
grace_seconds=${ABORT_BACKUP_EMERGENCY_GRACE_SECONDS:-8}
kill_wait_seconds=${ABORT_BACKUP_EMERGENCY_KILL_WAIT_SECONDS:-3}
if [[ ! "$grace_seconds" =~ ^[1-9][0-9]?$ ||
      ! "$kill_wait_seconds" =~ ^[1-9][0-9]?$ ]]; then
  echo "ERROR: invalid emergency backup-stop timeout" >&2
  exit 2
fi

[ -f "$job_lock" ] || exit 0
/usr/bin/flock -n "$job_lock" true 2>/dev/null && exit 0  # lock free -> nothing running

pid=$(tr -cd 0-9 < "$job_lock")
if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
  echo "asking backup/restore (pid $pid) to stop before unmount"
  kill -TERM "$pid"
fi

if (( ! emergency )); then
  if ! /usr/bin/flock -w 60 "$job_lock" true; then
    echo "ERROR: backup/restore did not release its lock within 60s" >&2
    exit 1
  fi
  exit 0
fi

if /usr/bin/flock -w "$grace_seconds" "$job_lock" true; then
  exit 0
fi

# Descendants inherit the backup lock descriptor, so selecting processes by
# the exact lock file kills only the still-running backup/restore tree without
# relying on process names or a potentially reused PID.
echo "backup/restore did not stop within ${grace_seconds}s; killing lock holders"
/usr/bin/fuser -k -KILL "$job_lock" >/dev/null 2>&1 || true
if ! /usr/bin/flock -w "$kill_wait_seconds" "$job_lock" true; then
  echo "ERROR: backup/restore still holds its lock after emergency kill" >&2
  exit 1
fi
