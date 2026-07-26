#! /bin/bash

md_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=disk_policy.sh
if ! . "$md_script_dir/disk_policy.sh"; then
  echo "ERROR: cannot load $md_script_dir/disk_policy.sh" >&2
  return 1 2>/dev/null || exit 1
fi
md_samba_share_control=${MOUNT_DISKS_SAMBA_SHARE_CONTROL:-"$md_script_dir/samba_share_control.sh"}

# Set by md_resolve_label on success.
MD_DEVICE=""
MD_LABEL_KEY=""
MD_MOUNT_SOURCE=""
MD_STALE_LABELS=()
MD_STALE_SOURCES=()

md_query_token() {
  local token="$1"
  local devices
  local status

  devices="$(/usr/bin/sudo /sbin/blkid -t "$token" -o device)"
  status=$?
  if (( status == 0 )); then
    if [[ -z "$devices" ]]; then
      echo "ERROR: blkid succeeded but returned no device for $token" >&2
      return 2
    fi
    printf '%s\n' "$devices"
    return 0
  fi

  # blkid returns 2 when no device matches a token.
  if (( status == 2 )) && [[ -z "$devices" ]]; then
    return 1
  fi

  echo "ERROR: blkid failed while resolving $token (status $status)" >&2
  return 2
}

md_resolve_label() {
  local label="$1"
  local label_devices
  local label_status
  local partlabel_devices
  local partlabel_status
  local devices
  local device
  local count
  local candidate
  local actual_label

  MD_DEVICE=""
  MD_LABEL_KEY=""
  if [[ -z "$label" ]]; then
    echo "ERROR: refusing to resolve an empty disk label" >&2
    return 2
  fi

  label_devices="$(md_query_token "LABEL=$label")"
  label_status=$?
  (( label_status == 2 )) && return 2

  partlabel_devices="$(md_query_token "PARTLABEL=$label")"
  partlabel_status=$?
  (( partlabel_status == 2 )) && return 2

  if (( label_status == 1 && partlabel_status == 1 )); then
    return 1
  fi

  devices="$({ printf '%s\n' "$label_devices"; printf '%s\n' "$partlabel_devices"; } \
    | /usr/bin/awk 'NF && !seen[$0]++')"
  count="$(printf '%s\n' "$devices" | /usr/bin/awk 'NF { count++ } END { print count + 0 }')"
  if (( count != 1 )); then
    echo "ERROR: exact label '$label' resolves to $count devices; refusing" >&2
    printf '%s\n' "$devices" >&2
    return 2
  fi

  device="$(printf '%s\n' "$devices" | /usr/bin/awk 'NF { print; exit }')"
  device="$(/usr/bin/readlink -f -- "$device")"
  if [[ -z "$device" || ! -b "$device" ]]; then
    echo "ERROR: resolved device for '$label' is not a block device" >&2
    return 2
  fi

  MD_LABEL_KEY="PARTLABEL"
  while IFS= read -r candidate; do
    if [[ -n "$candidate" && "$(/usr/bin/readlink -f -- "$candidate")" == "$device" ]]; then
      MD_LABEL_KEY="LABEL"
      break
    fi
  done <<< "$label_devices"

  actual_label="$(/usr/bin/sudo /sbin/blkid -s "$MD_LABEL_KEY" -o value -- "$device")"
  if [[ $? -ne 0 || "$actual_label" != "$label" ]]; then
    echo "ERROR: $device no longer has exact $MD_LABEL_KEY '$label'; refusing" >&2
    return 2
  fi

  MD_DEVICE="$device"
  return 0
}

md_parent_disk() {
  local device="$1"
  local ancestors
  local status
  local disks
  local count

  ancestors="$(/usr/bin/lsblk -s -nrpo NAME,TYPE -- "$device")"
  status=$?
  if (( status != 0 )) || [[ -z "$ancestors" ]]; then
    echo "ERROR: cannot determine parent disk for $device" >&2
    return 1
  fi

  disks="$(printf '%s\n' "$ancestors" \
    | /usr/bin/awk '$2 == "disk" && !seen[$1]++ { print $1 }')"
  count="$(printf '%s\n' "$disks" | /usr/bin/awk 'NF { count++ } END { print count + 0 }')"
  if (( count != 1 )); then
    echo "ERROR: expected one parent disk for $device, found $count" >&2
    return 1
  fi

  printf '%s\n' "$disks"
}

md_canonical_path() {
  /usr/bin/readlink -f -- "$1"
}

md_mount_source_is_live() {
  local source="$1"
  local resolved_source

  resolved_source="$(md_canonical_path "$source" 2>/dev/null)"
  [[ -n "$resolved_source" && -b "$resolved_source" ]]
}

md_clear_samba_drain() {
  local label=$1

  disk_policy_samba_share_name "$label" >/dev/null 2>&1 || return 0
  if [[ ! -x "$md_samba_share_control" ]]; then
    echo "ERROR: cannot clear Samba drain for $label: $md_samba_share_control is unavailable" >&2
    return 1
  fi
  "$md_samba_share_control" clear "$label"
}

# Return the one source mounted at exactly this path.  Unlike findmnt -T, -M
# does not mistake the root filesystem containing an unmounted directory for a
# mount on that directory.  Returns 0 for one mount, 1 for no mount, and 2 when
# the state cannot be determined safely.
md_find_exact_mount_source() {
  local pth="$1"
  local output
  local status
  local count

  MD_MOUNT_SOURCE=""
  output="$(/usr/bin/findmnt -rn -M "$pth" -o SOURCE 2>&1)"
  status=$?
  if (( status == 1 )) && [[ -z "$output" ]]; then
    return 1
  elif (( status != 0 )); then
    echo "ERROR: cannot determine whether $pth is a mount point (findmnt status $status)" >&2
    return 2
  fi

  count="$(printf '%s\n' "$output" | /usr/bin/awk 'NF { count++ } END { print count + 0 }')"
  if (( count != 1 )); then
    echo "ERROR: expected one source mounted at $pth, found $count; refusing" >&2
    return 2
  fi

  MD_MOUNT_SOURCE="$(printf '%s\n' "$output" | /usr/bin/awk 'NF { print; exit }')"
  return 0
}

# Determine whether the exact target is free, already has the intended device,
# or is occupied by something unsafe.  In particular, a /dev path which no
# longer exists is a stale kernel mount, not evidence that the replacement
# device is mounted correctly.
#
# Returns 0 when the intended device is already mounted, 1 when the target is
# unmounted and available for further checks, and 2 on an unsafe/unknown state.
md_check_existing_mount() {
  local label="$1"
  local pth="$2"
  local device="$3"
  local status
  local source
  local resolved_source

  md_find_exact_mount_source "$pth"
  status=$?
  (( status == 1 )) && return 1
  (( status == 0 )) || return 2

  source="$MD_MOUNT_SOURCE"
  resolved_source="$(md_canonical_path "$source" 2>/dev/null)"
  if [[ -n "$resolved_source" && "$resolved_source" == "$device" ]]; then
    echo "already mounted for: $label ($device)"
    return 0
  fi

  if [[ "$source" == /dev/* && ( -z "$resolved_source" || ! -b "$resolved_source" ) ]]; then
    echo "ERROR: stale mount at $pth still refers to vanished source $source; exact label '$label' now resolves to $device; refusing" >&2
  else
    echo "ERROR: $pth is mounted from unexpected source $source; exact label '$label' resolves to $device; refusing" >&2
  fi
  return 2
}

# Collect managed targets whose kernel mount still refers to a /dev node that
# vanished. This is distinct from an unexpected-but-live source, which remains
# a hard refusal and is never unmounted automatically.
md_collect_stale_mounts() {
  local label
  local pth
  local status
  local source

  MD_STALE_LABELS=()
  MD_STALE_SOURCES=()
  for label in "${MOUNT_LABELS[@]}"; do
    pth="/mnt/$label"
    md_find_exact_mount_source "$pth"
    status=$?
    if (( status == 1 )); then
      continue
    elif (( status != 0 )); then
      return 1
    fi

    source="$MD_MOUNT_SOURCE"
    if [[ "$source" == /dev/* ]] && ! md_mount_source_is_live "$source"; then
      MD_STALE_LABELS+=("$label")
      MD_STALE_SOURCES+=("$source")
    fi
  done
}

md_list_stale_mounts() {
  local index

  md_collect_stale_mounts || return 1
  for index in "${!MD_STALE_LABELS[@]}"; do
    printf '%s\t/mnt/%s\t%s\n' \
      "${MD_STALE_LABELS[$index]}" \
      "${MD_STALE_LABELS[$index]}" \
      "${MD_STALE_SOURCES[$index]}"
  done
}

md_normal_unmount_stale_target() {
  /usr/bin/sudo /usr/bin/timeout --kill-after=5 15 \
    /usr/bin/umount -- "$1"
}

md_recover_stale_mounts() {
  local index
  local label
  local pth
  local expected_source
  local status
  local output
  local had_failure=0

  # Finish discovery before changing any mount. A discovery error therefore
  # prevents partial cleanup across the managed target set.
  md_collect_stale_mounts || return 1
  if (( ${#MD_STALE_LABELS[@]} == 0 )); then
    echo "no stale managed mounts found"
    return 0
  fi

  for index in "${!MD_STALE_LABELS[@]}"; do
    label="${MD_STALE_LABELS[$index]}"
    pth="/mnt/$label"
    expected_source="${MD_STALE_SOURCES[$index]}"

    # Revalidate immediately before unmounting. Never touch a target that was
    # remounted or whose source became live after the initial scan.
    md_find_exact_mount_source "$pth"
    status=$?
    if (( status == 1 )); then
      echo "$label: stale mount disappeared before recovery"
      continue
    elif (( status != 0 )); then
      had_failure=1
      continue
    fi
    if [[ "$MD_MOUNT_SOURCE" != "$expected_source" ]]; then
      echo "ERROR: $pth source changed from $expected_source to $MD_MOUNT_SOURCE; refusing recovery" >&2
      had_failure=1
      continue
    fi
    if md_mount_source_is_live "$MD_MOUNT_SOURCE"; then
      echo "ERROR: $pth source $MD_MOUNT_SOURCE became live; refusing stale recovery" >&2
      had_failure=1
      continue
    fi

    echo "recovering stale mount for $label: $expected_source -> $pth"
    output="$(md_normal_unmount_stale_target "$pth" 2>&1)"
    status=$?
    if (( status != 0 )); then
      echo "ERROR: normal unmount failed for stale $label mount (status $status): ${output:-no diagnostic output}" >&2
      had_failure=1
      continue
    fi

    md_find_exact_mount_source "$pth"
    status=$?
    if (( status == 0 )); then
      echo "ERROR: $pth remains mounted after stale recovery" >&2
      had_failure=1
    elif (( status != 1 )); then
      had_failure=1
    else
      echo "recovered stale mount for $label"
    fi
  done

  (( had_failure == 0 ))
}

md_first_blocking_mount_dir_entry() {
  # Root's cron launches this script through `su pi -c` without changing out
  # of /root.  GNU find otherwise inspects the absolute target successfully,
  # then exits 1 because pi cannot restore that inaccessible starting cwd.
  # Finder's two standard metadata files are harmless underlay clutter. Only
  # regular files with those exact names are allowed; a directory, symlink, or
  # similarly named entry still blocks the mount.
  (
    cd / || {
      echo "ERROR: cannot enter a safe working directory for mount validation" >&2
      return 1
    }
    /usr/bin/timeout 5 /usr/bin/find "$1" -mindepth 1 -maxdepth 1 \
      ! \( -type f \( -name .DS_Store -o -name '._.DS_Store' \) \) \
      -print -quit
  )
}

md_require_empty_mount_dir() {
  local pth="$1"
  local blocking_entry
  local status

  if [[ ! -d "$pth" ]]; then
    echo "ERROR: mount target is not a directory: $pth" >&2
    return 1
  fi

  # A healthy local mount directory should answer immediately.  The timeout
  # also fails closed if a broken mount appears during the validation race.
  # Preserve stderr so a permission, I/O, or timeout failure is actionable in
  # the policy log instead of being reduced to an unexplained status number.
  blocking_entry="$(md_first_blocking_mount_dir_entry "$pth" 2>&1)"
  status=$?
  if (( status != 0 )); then
    echo "ERROR: cannot verify that the underlying mount directory $pth is empty (status $status); refusing" >&2
    if [[ -n "$blocking_entry" ]]; then
      printf 'directory probe diagnostic: %s\n' "$blocking_entry" >&2
    fi
    return 1
  fi
  if [[ -n "$blocking_entry" ]]; then
    echo "ERROR: underlying mount directory $pth is not empty; preserving its contents and refusing to mount over them" >&2
    return 1
  fi
  if [[ ( -f "$pth/.DS_Store" && ! -L "$pth/.DS_Store" ) ||
        ( -f "$pth/._.DS_Store" && ! -L "$pth/._.DS_Store" ) ]]; then
    echo "ignoring Finder metadata in underlying mount directory: $pth"
  fi
  return 0
}

mntdsk() {
  local label="$1"
  local pth="/mnt/$label"
  local resolve_status
  local status
  local device
  local label_key
  local mount_targets
  local read_only
  local fstype
  local root_source
  local root_disk
  local device_disk
  local actual_label
  local mounted_target
  local pi_uid pi_gid
  local -a mount_opts=()

  disk_health_is_quarantined "$label"
  status=$?
  if (( status == 0 )); then
    echo "ERROR: $label is quarantined after a failed filesystem check; repair it before mounting" >&2
    return 1
  elif (( status != 1 )); then
    return 1
  fi

  md_resolve_label "$label"
  resolve_status=$?
  if (( resolve_status == 1 )); then
    echo "no exact LABEL or PARTLABEL match for: $label"
    return 0
  elif (( resolve_status != 0 )); then
    return 1
  fi
  device="$MD_DEVICE"
  label_key="$MD_LABEL_KEY"

  md_check_existing_mount "$label" "$pth" "$device"
  status=$?
  if (( status == 0 )); then
    md_clear_samba_drain "$label"
    return $?
  fi
  (( status == 1 )) || return 1

  mount_targets="$(/usr/bin/findmnt -rn -S "$device" -o TARGET 2>&1)"
  status=$?
  if (( status == 0 )); then
    echo "ERROR: $device is already mounted somewhere other than $pth: $mount_targets" >&2
    return 1
  elif (( status != 1 )) || [[ -n "$mount_targets" ]]; then
    echo "ERROR: cannot determine whether $device is already mounted (findmnt status $status)" >&2
    return 1
  fi

  read_only="$(/usr/bin/lsblk -dnro RO -- "$device")"
  if [[ $? -ne 0 || ! "$read_only" =~ ^[01]$ ]]; then
    echo "ERROR: cannot determine read-only state for $device" >&2
    return 1
  elif [[ "$read_only" == "1" ]]; then
    echo "ERROR: refusing to mount read-only device for: $label ($device)" >&2
    return 1
  fi

  root_source="$(/usr/bin/findmnt -rn -T / -o SOURCE)"
  if [[ $? -ne 0 || -z "$root_source" ]]; then
    echo "ERROR: cannot determine root filesystem source; refusing to mount $label" >&2
    return 1
  fi
  root_disk="$(md_parent_disk "$root_source")" || return 1
  device_disk="$(md_parent_disk "$device")" || return 1
  if [[ "$root_disk" == "$device_disk" ]]; then
    echo "ERROR: refusing to mount $label from root disk $root_disk" >&2
    return 1
  fi

  fstype="$(/usr/bin/sudo /sbin/blkid -s TYPE -o value -- "$device")"
  if [[ $? -ne 0 || -z "$fstype" ]]; then
    echo "ERROR: cannot determine filesystem type for $label ($device)" >&2
    return 1
  fi

  if [[ "$fstype" == "hfsplus" ]]; then
    mount_opts=(-o force,rw)
  elif [[ "$fstype" == "exfat" ]]; then
    pi_uid=$(/usr/bin/id -u pi) || return 1
    pi_gid=$(/usr/bin/id -g pi) || return 1
    [[ "$pi_uid" =~ ^[0-9]+$ && "$pi_gid" =~ ^[0-9]+$ ]] || return 1
    mount_opts=(-o "uid=$pi_uid,gid=$pi_gid,fmask=0022,dmask=0022")
    /usr/bin/sudo /usr/sbin/modprobe fuse || return 1
  fi

  /usr/bin/sudo /usr/bin/install -d -m 0777 -o pi -g pi -- "$pth" || return 1
  md_check_existing_mount "$label" "$pth" "$device"
  status=$?
  if (( status == 0 )); then
    md_clear_samba_drain "$label"
    return $?
  fi
  (( status == 1 )) || return 1

  md_require_empty_mount_dir "$pth" || return 1

  # Close the discovery-to-mount race by checking the selected exact label again.
  actual_label="$(/usr/bin/sudo /sbin/blkid -s "$label_key" -o value -- "$device")"
  if [[ $? -ne 0 || "$actual_label" != "$label" || ! -b "$device" ]]; then
    echo "ERROR: $device no longer has exact $label_key '$label'; refusing" >&2
    return 1
  fi

  # Recheck after examining the underlying directory so another mount cannot
  # silently become an underlay between validation and the mount command.
  md_check_existing_mount "$label" "$pth" "$device"
  status=$?
  if (( status == 0 )); then
    md_clear_samba_drain "$label"
    return $?
  fi
  (( status == 1 )) || return 1

  echo "mounting exact $label_key '$label' from $device at $pth"
  /usr/bin/sudo /usr/bin/mount -t "$fstype" "${mount_opts[@]}" -- "$device" "$pth" || return 1

  mounted_target="$(/usr/bin/findmnt -rn -S "$device" -o TARGET 2>&1)"
  status=$?
  if (( status != 0 )) || [[ "$mounted_target" != "$pth" ]]; then
    echo "ERROR: mount command succeeded but $device is not mounted at $pth" >&2
    return 1
  fi
  md_clear_samba_drain "$label" || return 1
  echo "mounted $label at $pth"
}

rm_mnt_dir() { # prevent Time Machine from backing up onto SD card etc
  local diskdir="$1"
  local resolve_status

  # fail closed: this deleted the mounted TM disk on 2026-07-14 when bare
  # `blkid` wasn't in pi's SSH PATH → empty output looked like "disk absent"
  if /usr/bin/grep -q " /mnt/$diskdir " /proc/mounts; then
    echo "NOT removing mount dir /mnt/$diskdir: currently mounted"
    return 0
  fi

  md_resolve_label "$diskdir"
  resolve_status=$?
  if (( resolve_status == 0 )); then
    echo "NOT removing mount dir /mnt/$diskdir for attached disk $MD_DEVICE"
    return 0
  elif (( resolve_status != 1 )); then
    echo "ERROR: disk discovery failed, refusing to remove /mnt/$diskdir"
    return 1
  fi

  [[ -d "/mnt/$diskdir" ]] || return 0
  echo "removing empty mount dir because no exact label is attached: /mnt/$diskdir"
  # rmdir only: a decoy dir is always empty; anything non-empty is real data
  if ! /usr/bin/rmdir -- "/mnt/$diskdir"; then
    echo "ERROR: /mnt/$diskdir has contents, refusing to delete" >&2
    return 1
  fi
  return 0
}

md_fix_hfs_mounts() {
  if [[ ! -r /home/pi/scripts/fix_hfs_fs.sh ]]; then
    echo "ERROR: cannot read /home/pi/scripts/fix_hfs_fs.sh" >&2
    return 1
  fi
  # shellcheck source=fix_hfs_fs.sh
  . /home/pi/scripts/fix_hfs_fs.sh
}

md_print_mounts() {
  echo "mounted disks:"
  /usr/bin/grep "dev/sd" /proc/mounts || true
}

md_reconcile_labels() {
  local had_failure=0 hold_status hold_remaining disk

  for disk in "$@"; do
    hold_remaining=$(disk_eject_hold_remaining "$disk" 2>&1)
    hold_status=$?
    if (( hold_status == 0 )); then
      echo "$disk: temporarily ejected; automatic mount resumes in ${hold_remaining}s"
      continue
    elif (( hold_status != 1 )); then
      echo "$hold_remaining" >&2
      had_failure=1
      continue
    fi
    mntdsk "$disk" || had_failure=1
  done

  (( had_failure == 0 ))
}

mount_disks_main() {
  local had_failure=0
  local dir
  local disk
  local -a rm_dirs=(mbp1tbkup mbp2tbkup)
  local -a disks=("${MOUNT_LABELS[@]}")

  if (( $# == 1 )) && [[ "$1" == --list-stale ]]; then
    md_list_stale_mounts
    return $?
  elif (( $# == 1 )) && [[ "$1" == --recover-stale ]]; then
    md_recover_stale_mounts
    return $?
  elif (( $# == 1 )) && [[ "$1" == --always ]]; then
    md_reconcile_labels "${ALWAYS_MOUNT_LABELS[@]}" || had_failure=1
  elif (( $# == 1 )) && [[ "$1" == --* ]]; then
    echo "usage: ${0##*/} [label|--always|--list-stale|--recover-stale]" >&2
    return 2
  elif (( $# == 1 )); then
    mntdsk "$1" || had_failure=1
  elif (( $# == 0 )); then # used by the minutely policy job
    for dir in "${rm_dirs[@]}"; do
      rm_mnt_dir "$dir" || had_failure=1
    done

    # Reconcile every label, but remember any unsafe result so a later missing
    # optional disk cannot overwrite an earlier failure status.
    md_reconcile_labels "${disks[@]}" || had_failure=1
    # mntdsk mbbackup
    # mntdsk bigboi
  else
    echo "usage: ${0##*/} [label|--always|--list-stale|--recover-stale]" >&2
    return 2
  fi

  md_fix_hfs_mounts || had_failure=1
  md_print_mounts
  (( had_failure == 0 ))
}

# Source mode remains supported for existing interactive helpers.  Tests can
# request definitions only without performing live disk operations.
if [[ "${MOUNT_DISKS_LIBRARY_ONLY:-0}" != 1 ]]; then
  mount_disks_main "$@"
  mount_disks_rc=$?
  if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    exit "$mount_disks_rc"
  else
    return "$mount_disks_rc"
  fi
fi

# 
# 
# If the optional argument new-label is present, then e2label will set the filesystem label to be new-label. 
# Ext2 filesystem labels can be at most 16 characters long; if new-label is longer than 16 characters, 
# e2label will truncate it and print a warning message. To set a new label, enter:
# # e2label /dev/sdb2 usbstroage
# 
# It is also possible to set the filesystem label using the -L option of tune2fs, enter:
# # tune2fs -L usbstroage /dev/sdb2
