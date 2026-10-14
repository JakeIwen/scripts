#!/bin/bash
# Backup-job fence for umount_disks.sh: sourced by it, never run directly.
# Stops the jobs holding the disks this run will unmount or spin down and
# keeps those disks claimed until the lifecycle shell exits. Like the other
# private phases it reads umount_disks_main's dynamic scope (mounted_labels,
# attached_labels, spindown, emergency, dry_run) and checks its
# immediate caller before touching that state.

. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/backup_lifecycle.sh" || return 1

ud_abort_backup_jobs() { # <abort_backup.sh arguments>: stop jobs holding the named disks
  /usr/bin/sudo /home/pi/scripts/backup/abort_backup.sh "$@"
}

ud_fence_backup_jobs() {
  if [[ ${FUNCNAME[1]:-} != ud_unmount_all ]]; then
    echo "ERROR: ud_fence_backup_jobs may only be called by ud_unmount_all" >&2
    return 1
  fi

  # hd-idle stops every attached HDD, mounted or not, and a job that passed
  # its ignition check may still be mounting one while holding only its disk
  # lock. With --spindown, drain and claim every attached disk; otherwise only
  # the disks this run unmounts, so a job on a disk left untouched keeps running.
  local -a fence_labels=("${mounted_labels[@]}")
  (( spindown )) && fence_labels=("${attached_labels[@]}")
  (( ${#fence_labels[@]} )) || return 0

  if ! backup_lifecycle_claim ud_abort_backup_jobs "$emergency" "${fence_labels[@]}"; then
    ud_record_failure "backup/restore did not stop; refusing to unmount or spin down HDDs"
    ud_notify_failures "$(( spindown && ! dry_run ))"
    return 1
  fi
  return 0
}
