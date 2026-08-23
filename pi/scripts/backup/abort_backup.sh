#!/bin/bash
# Gracefully stop a running backup/restore before drives get unmounted, or stop
# one exact dashboard-visible backup without exposing restore/initialization
# jobs to the web UI. Called by umount_disks.sh (including the ignition-on
# nodisk path); its ordinary unmount mode remains a no-op when idle.
set -u
backup_conf=${ABORT_BACKUP_CONF:-/home/pi/scripts/backup/backup_conf.sh}
. "$backup_conf"

emergency=0
user_kind=
case "$#:${1:-}:${2:-}" in
  0::) ;;
  1:--emergency:) emergency=1 ;;
  2:--user:borg|2:--user:exfat) user_kind=$2 ;;
  *)
    echo "usage: ${0##*/} [--emergency | --user borg|exfat]" >&2
    exit 2
    ;;
esac

job_lock=${ABORT_BACKUP_JOB_LOCK:-$JOB_LOCK}
grace_seconds=${ABORT_BACKUP_EMERGENCY_GRACE_SECONDS:-8}
kill_wait_seconds=${ABORT_BACKUP_EMERGENCY_KILL_WAIT_SECONDS:-3}
user_grace_seconds=${ABORT_BACKUP_USER_GRACE_SECONDS:-120}
borg_tool=${ABORT_BACKUP_BORG_TOOL:-/home/pi/scripts/backup/pi_backup.sh}
exfat_tool=${ABORT_BACKUP_EXFAT_TOOL:-/home/pi/scripts/backup/exfat_snapshot.sh}
if [[ ! "$grace_seconds" =~ ^[1-9][0-9]?$ ||
      ! "$kill_wait_seconds" =~ ^[1-9][0-9]?$ ||
      ! "$user_grace_seconds" =~ ^[1-9][0-9]{0,2}$ ]] ||
   (( user_grace_seconds > 180 )); then
  echo "ERROR: invalid backup-stop timeout" >&2
  exit 2
fi

process_has_exact_argument() {
  local target_pid=$1 expected=$2 argument
  [[ -r "/proc/$target_pid/cmdline" ]] || return 1
  while IFS= read -r -d '' argument; do
    [[ "$argument" == "$expected" ]] && return 0
  done < "/proc/$target_pid/cmdline"
  return 1
}

process_holds_job_lock() {
  local target_pid=$1 expected_lock fd resolved
  expected_lock=$(/usr/bin/readlink -f -- "$job_lock") || return 1
  for fd in "/proc/$target_pid"/fd/*; do
    resolved=$(/usr/bin/readlink -f -- "$fd" 2>/dev/null) || continue
    [[ "$resolved" == "$expected_lock" ]] && return 0
  done
  return 1
}

if [[ ! -f "$job_lock" ]]; then
  [[ -z "$user_kind" ]] && exit 0
  echo "ERROR: no active $user_kind backup lock was found" >&2
  exit 3
fi
if /usr/bin/flock -n "$job_lock" true 2>/dev/null; then
  [[ -z "$user_kind" ]] && exit 0
  echo "ERROR: no active $user_kind backup is holding the shared lock" >&2
  exit 3
fi

pid=$(/bin/cat -- "$job_lock" 2>/dev/null) || pid=
if [[ -n "$user_kind" ]]; then
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || {
    echo "ERROR: active backup lock has no valid holder PID" >&2
    exit 3
  }
  expected_tool=$borg_tool
  [[ "$user_kind" == exfat ]] && expected_tool=$exfat_tool
  if ! kill -0 "$pid" 2>/dev/null ||
     ! process_has_exact_argument "$pid" "$expected_tool" ||
     ! process_holds_job_lock "$pid"; then
    echo "ERROR: shared lock holder is not the active $user_kind backup; refusing to stop it" >&2
    exit 3
  fi
  echo "asking $user_kind backup (pid $pid) to stop at the user's request"
  kill -USR1 "$pid"
  stop_deadline=$((SECONDS + user_grace_seconds))
  if ! /usr/bin/flock -w "$user_grace_seconds" "$job_lock" true; then
    echo "ERROR: $user_kind backup did not release its lock within ${user_grace_seconds}s" >&2
    exit 1
  fi
  while kill -0 "$pid" 2>/dev/null && (( SECONDS < stop_deadline )); do
    /bin/sleep 1
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "ERROR: $user_kind backup released its lock but did not finish stopping" >&2
    exit 1
  fi
  exit 0
fi

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
