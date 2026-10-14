#!/bin/bash
# Resource-specific backup locks, sourced by backup_conf.sh after the legacy
# JOB_LOCK_* settings. See pi/docs/backup/CONCURRENCY.md for the registry and
# the conflict matrix.
#
# Every lock is a pre-created file (systemd-tmpfiles, root:pi 0660) inside
# BACKUP_LOCK_DIR. Acquisition never creates files and fails closed on missing
# or unsafe metadata. Descriptors: fd 9 holds the legacy lock in shared mode,
# fd 10 holds this job's exclusive identity lock with its PID record, and
# disks, data sets and the cloud slot use dynamically allocated descriptors
# that children inherit.

# Lifecycle callers source this library without the full backup configuration.
: "${JOB_LOCK:=${VANPI_BACKUP_JOB_LOCK:-/run/lock/vanpi_backup.lock}}"
: "${JOB_LOCK_OWNER:=${VANPI_BACKUP_JOB_LOCK_OWNER:-root}}"
: "${JOB_LOCK_GROUP:=${VANPI_BACKUP_JOB_LOCK_GROUP:-pi}}"
: "${JOB_LOCK_MODE:=${VANPI_BACKUP_JOB_LOCK_MODE:-660}}"
: "${JOB_LOCK_STAT:=${VANPI_BACKUP_JOB_LOCK_STAT:-/usr/bin/stat}}"
: "${JOB_LOCK_FLOCK:=${VANPI_BACKUP_JOB_LOCK_FLOCK:-/usr/bin/flock}}"

BACKUP_LOCK_DIR=${VANPI_BACKUP_LOCK_DIR:-/run/lock/vanpi-backup}
BACKUP_LOCK_DIR_MODE=${VANPI_BACKUP_LOCK_DIR_MODE:-770}
BACKUP_LOCK_FD_LEGACY=9
BACKUP_LOCK_FD_JOB=10
BACKUP_JOB_KINDS=(borg exfat pi-cloud time-machine clone)
# Every label disk_policy.sh manages, so lifecycle drains can name any of them.
BACKUP_DISK_LABELS=(bigboi hdd1tb mbp2tbkup movingparts EXFAT512)
BACKUP_DATA_NAMES=(borg-repo)
BACKUP_CLOUD_SLOT=upload
BACKUP_JOB_KIND=
declare -A BACKUP_DISK_LOCK_FD=()
declare -A BACKUP_EXCLUSIVE_LOCK_FD=()

backup_lock_known() {
  local item=$1 candidate
  shift
  for candidate in "$@"; do
    [[ "$candidate" == "$item" ]] && return 0
  done
  return 1
}

backup_lock_path() { # <job|disk|data|cloud> <name> -> prints the lock path
  local class=$1 name=$2
  case "$class" in
    job) backup_lock_known "$name" "${BACKUP_JOB_KINDS[@]}" || return 1 ;;
    disk) backup_lock_known "$name" "${BACKUP_DISK_LABELS[@]}" || return 1 ;;
    data) backup_lock_known "$name" "${BACKUP_DATA_NAMES[@]}" || return 1 ;;
    cloud) [[ "$name" == "$BACKUP_CLOUD_SLOT" ]] || return 1 ;;
    *) return 1 ;;
  esac
  printf '%s/%s-%s.lock\n' "$BACKUP_LOCK_DIR" "$class" "$name"
}

backup_lock_metadata_ok() { # <path> <expected-mode>
  local path=$1 expected_mode=$2 metadata owner group mode extra
  metadata=$("$JOB_LOCK_STAT" -c '%U %G %a' -- "$path" 2>&1) || {
    echo "cannot inspect backup lock $path: $metadata" >&2
    return 1
  }
  read -r owner group mode extra <<< "$metadata"
  if [[ "$owner" != "$JOB_LOCK_OWNER" || "$group" != "$JOB_LOCK_GROUP" ||
        "$mode" != "$expected_mode" || -n ${extra:-} ]]; then
    echo "unsafe backup lock metadata for $path (owner=${owner:-unknown}, group=${group:-unknown}, mode=${mode:-unknown}; expected $JOB_LOCK_OWNER:$JOB_LOCK_GROUP $expected_mode)" >&2
    return 1
  fi
}

backup_lock_validate() { # <path>: pre-created regular file inside the validated registry
  local path=$1
  if [[ -L "$BACKUP_LOCK_DIR" || ! -d "$BACKUP_LOCK_DIR" ]]; then
    echo "backup lock directory is missing or unsafe: $BACKUP_LOCK_DIR" >&2
    return 1
  fi
  backup_lock_metadata_ok "$BACKUP_LOCK_DIR" "$BACKUP_LOCK_DIR_MODE" || return 1
  if [[ -L "$path" || ! -f "$path" ]]; then
    echo "backup lock is missing or unsafe: $path" >&2
    return 1
  fi
  backup_lock_metadata_ok "$path" "$JOB_LOCK_MODE"
}

hold_backup_disk() { # <label>: shared, non-blocking; fails while the disk is being drained
  local label=$1 path fd
  path=$(backup_lock_path disk "$label") || {
    echo "unknown backup disk label: $label" >&2
    return 1
  }
  [[ -z ${BACKUP_DISK_LOCK_FD[$label]:-} ]] && {
    backup_lock_validate "$path" || return 1
    exec {fd}>>"$path" || return 1
    if ! "$JOB_LOCK_FLOCK" -n -s "$fd"; then
      echo "$label is being unmounted or drained; cannot use it now" >&2
      exec {fd}>&-
      return 1
    fi
    BACKUP_DISK_LOCK_FD[$label]=$fd
  }
  return 0
}

hold_backup_disks_exclusive() { # <label ...>: lifecycle owns disks until this shell exits
  local label path fd
  local -a held_fds=()
  # Restore and hotspare initialization never consult disk locks: they only
  # take the legacy lock exclusively. Holding it shared here keeps them out
  # between the drain and the unmount, exactly like a running job would.
  if [[ -e "$JOB_LOCK" || -L "$JOB_LOCK" ]]; then
    if [[ -L "$JOB_LOCK" || ! -f "$JOB_LOCK" ]] ||
       ! backup_lock_metadata_ok "$JOB_LOCK" "$JOB_LOCK_MODE" ||
       ! exec {fd}<"$JOB_LOCK"; then
      echo "backup job lock is unsafe; refusing to unmount" >&2
      return 1
    fi
    held_fds+=("$fd")
    if ! "$JOB_LOCK_FLOCK" -n -s "$fd"; then
      exec {fd}<&-
      echo "a restore or maintenance job holds the backup lock; refusing to unmount" >&2
      return 1
    fi
  fi
  # A missing registry cannot admit jobs, so the legacy hold above is the only
  # exclusion left to keep. Unsafe registries still fail closed below.
  [[ -e "$BACKUP_LOCK_DIR" || -L "$BACKUP_LOCK_DIR" ]] || return 0
  for label in "$@"; do
    if path=$(backup_lock_path disk "$label") &&
       [[ ! -L "$BACKUP_LOCK_DIR" && -d "$BACKUP_LOCK_DIR" ]] &&
       backup_lock_metadata_ok "$BACKUP_LOCK_DIR" "$BACKUP_LOCK_DIR_MODE"; then
      # Missing inodes cannot admit jobs either; never recreate one here.
      [[ -e "$path" || -L "$path" ]] || continue
      if backup_lock_validate "$path" && exec {fd}<"$path"; then
        held_fds+=("$fd")
        "$JOB_LOCK_FLOCK" -n "$fd" && continue
      fi
    fi
    for fd in "${held_fds[@]}"; do
      exec {fd}<&-
    done
    echo "$label is in use by a backup job; refusing to unmount" >&2
    return 1
  done
  # Leave the descriptors open through unmount and spindown, not just the drain.
  return 0
}

release_backup_disk() { # <label>: releases only this job's own hold
  local label=$1 fd
  fd=${BACKUP_DISK_LOCK_FD[$label]:-}
  [[ -n "$fd" ]] || return 0
  # Close, never unlock: an explicit unlock would also strip a child that
  # inherited this descriptor, while the close ends the lock only with the
  # last descriptor (the orphan guarantee in CONCURRENCY.md).
  exec {fd}>&-
  unset "BACKUP_DISK_LOCK_FD[$label]"
}

release_backup_disk_if_unused() { # <label>: 0 when no other job holds the disk
  # Drops this job's shared hold, then tries an exclusive hold. Success means
  # the caller may unmount; the exclusive hold stays until release_backup_disk
  # so no job starts using the disk mid-unmount. Failure means another job (or
  # the lifecycle drain) still owns it, so the caller must leave it mounted.
  local label=$1 path fd
  release_backup_disk "$label"
  path=$(backup_lock_path disk "$label") || return 1
  backup_lock_validate "$path" || return 1
  exec {fd}>>"$path" || return 1
  if "$JOB_LOCK_FLOCK" -n "$fd"; then
    BACKUP_DISK_LOCK_FD[$label]=$fd
    return 0
  fi
  exec {fd}>&-
  return 1
}

acquire_backup_lock() { # <job|data|cloud> <name> [wait-seconds]: exclusive; non-blocking by default
  local class=$1 name=$2 wait=${3:-0} key="$1-$2" path fd deadline acquired=0
  path=$(backup_lock_path "$class" "$name") || {
    echo "unknown backup lock: $key" >&2
    return 1
  }
  [[ -z ${BACKUP_EXCLUSIVE_LOCK_FD[$key]:-} ]] || return 0
  [[ "$wait" =~ ^[0-9]+$ ]] || return 1
  backup_lock_validate "$path" || return 1
  exec {fd}>>"$path" || return 1
  if (( wait > 0 )); then
    deadline=$((SECONDS + wait))
    # Bash defers traps during foreground commands: bound each wait to a second.
    while (( SECONDS <= deadline )); do
      if "$JOB_LOCK_FLOCK" -n "$fd"; then
        acquired=1
        break
      fi
      (( SECONDS < deadline )) || break
      /bin/sleep 1
    done
    (( acquired ))
  else
    "$JOB_LOCK_FLOCK" -n "$fd"
  fi || {
    echo "backup lock $key is busy" >&2
    exec {fd}>&-
    return 1
  }
  BACKUP_EXCLUSIVE_LOCK_FD[$key]=$fd
  [[ "$class" == cloud ]] && export VANPI_BACKUP_CLOUD_SLOT_FD="$fd"
  return 0
}

release_backup_lock() { # <job|data|cloud> <name>
  local key="$1-$2" fd
  fd=${BACKUP_EXCLUSIVE_LOCK_FD[$key]:-}
  [[ -n "$fd" ]] || return 0
  exec {fd}>&-
  unset "BACKUP_EXCLUSIVE_LOCK_FD[$key]"
  [[ "$1" == cloud ]] && unset VANPI_BACKUP_CLOUD_SLOT_FD
  return 0
}

release_backup_job() { # releases every descriptor this job acquired, in reverse order
  local key label
  for key in "${!BACKUP_EXCLUSIVE_LOCK_FD[@]}"; do
    release_backup_lock "${key%%-*}" "${key#*-}"
  done
  for label in "${!BACKUP_DISK_LOCK_FD[@]}"; do
    release_backup_disk "$label"
  done
  exec 10>&-
  exec 9>&-
  BACKUP_JOB_KIND=
  unset VANPI_BACKUP_JOB_KIND VANPI_BACKUP_JOB_LOCK_FD VANPI_BACKUP_JOB_LOCK_PATH VANPI_BACKUP_CLOUD_SLOT_FD
}

acquire_backup_job() { # <kind> [disk-label ...]: legacy shared + exclusive job identity + disks
  # Order matters: the legacy lock first (an exclusive legacy holder excludes
  # everything), then the job identity, then priority admission for cloud
  # kinds, then each declared disk. Any failure releases what was acquired.
  local kind=$1 path label
  shift
  path=$(backup_lock_path job "$kind") || {
    echo "unknown backup job kind: $kind" >&2
    return 1
  }
  validate_job_lock_file || return 1
  backup_lock_validate "$path" || return 1
  exec 9>>"$JOB_LOCK" || return 1
  if ! "$JOB_LOCK_FLOCK" -n -s 9; then
    echo "an exclusive backup, restore or maintenance job is active" >&2
    exec 9>&-
    return 1
  fi
  exec 10>>"$path" || { exec 9>&-; return 1; }
  if ! "$JOB_LOCK_FLOCK" -n 10; then
    echo "another $kind backup job is active" >&2
    exec 10>&- 9>&-
    return 1
  fi
  if [[ "$kind" == pi-cloud || "$kind" == time-machine ]]; then
    if [[ -f "$BACKUP_PRIORITY_TOOL" ]]; then
      if ! /usr/bin/python3 "$BACKUP_PRIORITY_TOOL" --admit "$kind"; then
        release_backup_job
        return 1
      fi
    elif [[ -d /var/lib/vanpi-backup-priority ]]; then
      echo 'backup priority helper is unavailable; refusing admission' >&2
      release_backup_job
      return 1
    fi
  fi
  for label in "$@"; do
    hold_backup_disk "$label" || { release_backup_job; return 1; }
  done
  printf '%s\n' "$$" > "$path"
  BACKUP_JOB_KIND=$kind
  export VANPI_BACKUP_JOB_KIND="$kind" VANPI_BACKUP_JOB_LOCK_FD="$BACKUP_LOCK_FD_JOB" \
    VANPI_BACKUP_JOB_LOCK_PATH="$path" VANPI_BACKUP_LOCK_DIR="$BACKUP_LOCK_DIR"
}
