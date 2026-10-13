#!/bin/bash
# Drain only the requested backup disks before lifecycle changes, or ask one
# verified dashboard-visible job to stop without affecting its peers.
set -u
backup_conf=${ABORT_BACKUP_CONF:-/home/pi/scripts/backup/backup_conf.sh}
. "$backup_conf"

emergency=0
user_kind=
target_labels=()
usage() {
  echo "usage: ${0##*/} [--emergency] [LABEL ...] | --user borg|exfat" >&2
  exit 2
}
if [[ ${1:-} == --user ]]; then
  case "$#:$1:${2:-}" in
    2:--user:borg|2:--user:exfat) user_kind=$2; shift 2 ;;
    *) usage ;;
  esac
fi
while (( $# )); do
  case "$1" in
    --emergency)
      (( ${#target_labels[@]} == 0 )) || usage
      emergency=1
      shift
      ;;
    --*) usage ;;
    *)
      backup_lock_path disk "$1" >/dev/null || {
        echo "ERROR: unknown backup disk label: $1" >&2
        exit 2
      }
      target_labels+=("$1")
      shift
      ;;
  esac
done
(( ${#target_labels[@]} )) || target_labels=("${BACKUP_DISK_LABELS[@]}")

job_lock=${ABORT_BACKUP_JOB_LOCK:-$JOB_LOCK}
grace_seconds=${ABORT_BACKUP_EMERGENCY_GRACE_SECONDS:-8}
kill_wait_seconds=${ABORT_BACKUP_EMERGENCY_KILL_WAIT_SECONDS:-3}
user_grace_seconds=${ABORT_BACKUP_USER_GRACE_SECONDS:-120}
borg_tool=${ABORT_BACKUP_BORG_TOOL:-/home/pi/scripts/backup/pi_backup.sh}
exfat_tool=${ABORT_BACKUP_EXFAT_TOOL:-/home/pi/scripts/backup/exfat_snapshot.sh}
flock_command=${ABORT_BACKUP_FLOCK:-/usr/bin/flock}
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

process_holds_job_lock() { # <pid> <path>: compare inodes, not path spellings
  local target_pid=$1 path=$2 expected_lock fd identity
  expected_lock=$(/usr/bin/stat -Lc '%d:%i' -- "$path") || return 1
  for fd in "/proc/$target_pid"/fd/*; do
    identity=$(/usr/bin/stat -Lc '%d:%i' -- "$fd" 2>/dev/null) || continue
    [[ "$identity" == "$expected_lock" ]] && return 0
  done
  return 1
}

recorded_holder() { # <path>: advisory records alone never authorize a signal
  local path=$1 pid
  pid=$(/bin/cat -- "$path" 2>/dev/null) || return 1
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  process_holds_job_lock "$pid" "$path" || return 1
  printf '%s\n' "$pid"
}

registered_lock_present() { # 2 means absent, 1 means unsafe
  local path=$1
  # Missing files cannot have admitted a job. Symlinks are never "absent".
  [[ -e "$path" || -L "$path" ]] || return 2
  backup_lock_validate "$path"
}

wait_for_lock() { # <path> <seconds> <shared|exclusive>
  local path=$1 seconds=$2 mode=$3 fd rc
  local -a options=()
  [[ "$mode" == shared ]] && options=(-s)
  # Never create a lock during an idle check. Close before fuser is invoked,
  # otherwise emergency eviction would also kill this drain process.
  exec {fd}<"$path" || return 1
  "$flock_command" -w "$seconds" "${options[@]}" "$fd"
  rc=$?
  if (( rc == 0 )); then
    "$flock_command" -u "$fd" || rc=1
  fi
  exec {fd}<&-
  return "$rc"
}

wait_for_drain() { # <path> <description> <shared|exclusive>
  local lock_path=$1 description=$2 mode=$3 timeout=60
  (( emergency )) && timeout=$grace_seconds
  wait_for_lock "$lock_path" "$timeout" "$mode" && return 0
  if (( ! emergency )); then
    echo "ERROR: backup/restore did not release $description within 60s" >&2
    return 1
  fi
  echo "backup/restore did not stop within ${grace_seconds}s; killing lock holders for $description"
  /usr/bin/fuser -k -KILL "$lock_path" >/dev/null 2>&1 || true
  if ! wait_for_lock "$lock_path" "$kill_wait_seconds" "$mode"; then
    echo "ERROR: backup/restore still holds $description after emergency kill" >&2
    return 1
  fi
}

stop_user_job() {
  local path pid expected_tool=$borg_tool job_lock_fd rc stop_deadline
  path=$(backup_lock_path job "$user_kind") || return 3
  registered_lock_present "$path"
  rc=$?
  if (( rc == 2 )); then
    echo "ERROR: no active $user_kind backup lock was found" >&2
    return 3
  elif (( rc != 0 )); then
    return 1
  fi
  exec {job_lock_fd}<"$path" || return 1
  if "$flock_command" -n "$job_lock_fd"; then
    "$flock_command" -u "$job_lock_fd" || return 1
    echo "ERROR: no active $user_kind backup is holding its job lock" >&2
    return 3
  fi
  [[ "$user_kind" == exfat ]] && expected_tool=$exfat_tool
  if ! pid=$(recorded_holder "$path") ||
     ! process_has_exact_argument "$pid" "$expected_tool"; then
    echo "ERROR: job lock holder is not the active $user_kind backup; refusing to stop it" >&2
    return 3
  fi
  echo "asking $user_kind backup (pid $pid) to stop at the user's request"
  kill -USR1 "$pid" || return 1
  stop_deadline=$((SECONDS + user_grace_seconds))
  if ! "$flock_command" -w "$user_grace_seconds" "$job_lock_fd"; then
    echo "ERROR: $user_kind backup did not release its lock within ${user_grace_seconds}s" >&2
    return 1
  fi
  "$flock_command" -u "$job_lock_fd" || return 1
  exec {job_lock_fd}<&-
  while kill -0 "$pid" 2>/dev/null && (( SECONDS < stop_deadline )); do
    /bin/sleep 1
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "ERROR: $user_kind backup released its lock but did not finish stopping" >&2
    return 1
  fi
}

stop_legacy_holder() {
  local job_lock_fd pid
  if [[ -L "$job_lock" || ( -e "$job_lock" && ! -f "$job_lock" ) ]]; then
    echo "ERROR: backup job lock is unsafe; refusing it" >&2
    return 1
  fi
  [[ -e "$job_lock" ]] || return 0
  # Shared migrated jobs are not legacy blockers; disk locks identify them.
  if ! exec {job_lock_fd}<"$job_lock"; then
    echo "ERROR: cannot open existing backup job lock" >&2
    return 1
  fi
  if "$flock_command" -n -s "$job_lock_fd"; then
    "$flock_command" -u "$job_lock_fd" || return 1
    exec {job_lock_fd}<&-
    return 0
  fi
  exec {job_lock_fd}<&-
  if pid=$(recorded_holder "$job_lock"); then
    echo "asking backup/restore (pid $pid) to stop before unmount"
    kill -TERM "$pid" 2>/dev/null || true
  fi
  wait_for_drain "$job_lock" "its lock" shared
}

collect_disk_targets() {
  local label path rc
  disk_paths=()
  disk_labels=()
  [[ -e "$BACKUP_LOCK_DIR" || -L "$BACKUP_LOCK_DIR" ]] || return 0
  if [[ -L "$BACKUP_LOCK_DIR" || ! -d "$BACKUP_LOCK_DIR" ]]; then
    echo "ERROR: backup lock directory is unsafe" >&2
    return 1
  fi
  backup_lock_metadata_ok "$BACKUP_LOCK_DIR" "$BACKUP_LOCK_DIR_MODE" || return 1
  for label in "${target_labels[@]}"; do
    path=$(backup_lock_path disk "$label") || return 1
    registered_lock_present "$path"
    rc=$?
    (( rc == 2 )) && continue
    (( rc == 0 )) || return 1
    disk_paths+=("$path")
    disk_labels+=("$label")
  done
}

signal_disk_holders() {
  local kind path pid disk_path rc
  local -A signaled=()
  for kind in "${BACKUP_JOB_KINDS[@]}"; do
    path=$(backup_lock_path job "$kind") || return 1
    registered_lock_present "$path"
    rc=$?
    (( rc == 2 )) && continue
    (( rc == 0 )) || return 1
    pid=$(recorded_holder "$path") || continue
    [[ -z ${signaled[$pid]:-} ]] || continue
    for disk_path in "${disk_paths[@]}"; do
      if process_holds_job_lock "$pid" "$disk_path"; then
        echo "asking backup/restore (pid $pid) to stop before unmount"
        kill -TERM "$pid" 2>/dev/null || true
        signaled[$pid]=1
        break
      fi
    done
  done
}

if [[ -n "$user_kind" ]]; then
  stop_user_job
  exit $?
fi
collect_disk_targets || exit 1
stop_legacy_holder || exit 1
if (( ${#disk_paths[@]} )); then
  signal_disk_holders || exit 1
  for index in "${!disk_paths[@]}"; do
    wait_for_drain "${disk_paths[$index]}" "${disk_labels[$index]}" exclusive || exit 1
  done
fi
exit 0
