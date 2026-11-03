#!/bin/bash
# Fixed bridge to the existing label resolver, mounter and release helper.
set -u
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
. "$here/backup_conf.sh" || exit 1
MOUNT_DISKS_LIBRARY_ONLY=1
. "$here/../mount_disks.sh" || exit 1

verify() {
  disk_policy_resolve_exact_label "$1" label-only || return 1
  local source
  source=$(/usr/bin/findmnt -rn -M "/mnt/$1" -o SOURCE) || return 1
  [[ "$source" == "$DISK_POLICY_RESOLVED_DEVICE" ]] || return 1
}

case "${1:-}" in
  policy)
    (( $# == 1 )) || exit 2
    printf '%s\n' "${MOUNT_LABELS[@]}" "${MANUAL_MOUNT_LABELS[@]}" "${HDD_LABELS[@]}"
    for target in "${CLONE_TARGETS[@]}"; do printf '%s\n' "${target%%:*}"; done
    ;;
  resolve)
    (( $# == 2 )) || exit 2
    disk_policy_resolve_exact_label "$2" label-only || exit 1
    printf '%s\n' "$DISK_POLICY_RESOLVED_DEVICE"
    ;;
  mount|release)
    (( $# == 2 )) && [[ "$2" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,15}$ ]] || exit 2
    PYTHONPATH="$here" /usr/bin/python3 -P -B -c 'import backup_locks; backup_locks.require_held(9, backup_locks.legacy_lock())' || exit 1
    if [[ "$1" == mount ]]; then
      [[ ! -e "$IGNITION_FLAG" ]] || exit 1
      mntdsk "$2" || exit 1
      verify "$2" || exit 1
    else
      # umount_disks.sh would abort our own legacy holder. Under our existing
      # exclusion, use the normal release claim and a non-forced exact unmount.
      release_backup_disk_if_unused "$2" || exit 1
      verify "$2" || exit 1
      /usr/bin/umount -- "/mnt/$2" || exit 1
      release_backup_disk "$2"
    fi
    ;;
  *) exit 2 ;;
esac
