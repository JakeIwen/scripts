#!/bin/bash
# Fixed bridge to the existing label resolver, mounter and release helper.
# Mount and release accept one of two explicit admissions (OFFSITE_ADMISSION):
# "prepare" (prepare-disk holds the legacy lock exclusively on fd 9) or "job"
# (an offsite run holds job-offsite exclusively and, to mount, the label's disk
# lock shared on an inherited descriptor). See pi/docs/backup/CONCURRENCY.md.
set -u
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
. "$here/backup_conf.sh" || exit 1
MOUNT_DISKS_LIBRARY_ONLY=1
. "$here/../mount_disks.sh" || exit 1
# Release exit statuses, mapped by offsite_run.py: another holder keeps the
# disk mounted, or it was already unmounted (by a drain); neither is an error.
OFFSITE_RELEASE_BUSY=3
OFFSITE_RELEASE_ABSENT=4
# util-linux flock -n exits 1 only on contention (its default -E conflict code);
# any other status is an operational error, never "another holder".
FLOCK_CONFLICT=1

verify() {
  disk_policy_resolve_exact_label "$1" label-only || return 1
  md_find_exact_mount_source "/mnt/$1" || return 1
  [[ "$MD_MOUNT_SOURCE" == "$DISK_POLICY_RESOLVED_DEVICE" ]] || return 1
}

admitted() { # <prepare|job> <mount|release> <label>: prove inherited holds, never acquire them
  [[ "$1" == prepare || "$1" == job ]] || {
    echo 'offsite mount helper requires OFFSITE_ADMISSION=job or prepare' >&2
    return 1
  }
  PYTHONPATH="$here" /usr/bin/python3 -P -B - "$@" <<'PY'
import os
import sys

import backup_admission
import backup_locks as locks


def admit(mode, operation, label):
    if mode == 'prepare':
        locks.require_held(9, locks.legacy_lock())
        return
    locks.validate_registry()
    job_fd = backup_admission.descriptor_number(
        backup_admission.required_value(os.environ, 'VANPI_BACKUP_JOB_LOCK_FD'))
    backup_admission.validated_descriptor(job_fd, locks.lock_path(locks.LockClass.JOB, 'offsite'),
                                          exclusive=True)
    if operation != 'mount':
        return
    disks = {}
    for record in os.environ.get('VANPI_BACKUP_DISK_LOCK_FDS', '').split(','):
        fields = record.split('=')
        if len(fields) != 2 or fields[0] in disks:
            raise RuntimeError('offsite mount admission has invalid disk descriptors')
        disks[fields[0]] = backup_admission.descriptor_number(fields[1])
    if label not in disks:
        raise RuntimeError('offsite mount admission has no disk descriptor for ' + label)
    backup_admission.validated_descriptor(disks[label], locks.lock_path(locks.LockClass.DISK, label),
                                          exclusive=False)


try:
    admit(*sys.argv[1:])
except (OSError, RuntimeError, ValueError, KeyError) as error:
    raise SystemExit('offsite mount helper refused: ' + str(error))
PY
}

release() { # <label>: the caller already closed its own disk descriptor
  local label=$1 path fd status
  md_find_exact_mount_source "/mnt/$label"
  status=$?
  (( status == 1 )) && exit "$OFFSITE_RELEASE_ABSENT"
  (( status == 0 )) || exit 1
  path=$(backup_lock_path disk "$label") || exit 1
  # umount_disks.sh would drain this run itself. Prove no other holder instead:
  # a busy exclusive try means a peer job or a lifecycle drain owns the disk.
  backup_lock_open fd "$path" backup_lock_validate || exit 1
  "$JOB_LOCK_FLOCK" -n "$fd"
  status=$?
  (( status == FLOCK_CONFLICT )) && exit "$OFFSITE_RELEASE_BUSY"
  (( status == 0 )) || exit 1
  md_find_exact_mount_source "/mnt/$label"
  status=$?
  (( status == 1 )) && exit "$OFFSITE_RELEASE_ABSENT"
  (( status == 0 )) || exit 1
  verify "$label" || exit 1
  /usr/bin/umount -- "/mnt/$label" || exit 1
  # Close only after the unmount so no job starts using the disk mid-unmount.
  exec {fd}>&-
  exit 0
}

case "${1:-}" in
  policy)
    # Every policy label except the offsite drives, which prepare-disk may create.
    (( $# == 1 )) || exit 2
    {
      printf '%s\n' "${MOUNT_LABELS[@]}" "${MANUAL_MOUNT_LABELS[@]}" "${HDD_LABELS[@]}"
      for target in "${CLONE_TARGETS[@]}"; do printf '%s\n' "${target%%:*}"; done
    } | /usr/bin/grep -vxF -f <(printf '%s\n' "${OFFSITE_LABELS[@]}")
    ;;
  offsite)
    (( $# == 1 )) || exit 2
    printf '%s\n' "${OFFSITE_LABELS[@]}"
    ;;
  resolve)
    (( $# == 2 )) || exit 2
    disk_policy_resolve_exact_label "$2" label-only || exit 1
    printf '%s\n' "$DISK_POLICY_RESOLVED_DEVICE"
    ;;
  mount|release)
    (( $# == 2 )) && [[ "$2" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$ ]] || exit 2
    admitted "${OFFSITE_ADMISSION:-}" "$1" "$2" || exit 1
    if [[ "$1" == mount ]]; then
      # The run passes the same flag its own guard polls (offsite_config.py).
      [[ ! -e "${OFFSITE_IGNITION_FLAG:-$IGNITION_FLAG}" ]] || exit 1
      mntdsk "$2" || exit 1
      verify "$2" || exit 1
    else
      release "$2"
    fi
    ;;
  *) exit 2 ;;
esac
