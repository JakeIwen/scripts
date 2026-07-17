#!/bin/bash

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=disk_policy.sh
if ! . "$script_dir/disk_policy.sh"; then
  echo "ERROR: cannot load $script_dir/disk_policy.sh" >&2
  return 1 2>/dev/null || exit 1
fi

UD_DEVICE=
UD_PARENT=
UD_MOUNTS=
UD_FAST_DEVICE=
UD_FAST_PARENT=
UD_FAST_DISKSEQ=
UD_FAILURES=()
UD_STATE_DIR=/run/lock/vanpi-hdd-spindown

ud_usage() {
  echo "usage: ${0##*/} [--dry-run] [--spindown] [label]" >&2
  echo "       ${0##*/} --clear-spindown-state" >&2
}

# Build an identity using only udev symlinks and sysfs.  Unlike blkid, this
# does not issue reads to a sleeping disk.  diskseq changes when a block device
# is disconnected and another device later reuses the same /dev/sdX name.
ud_fast_identity() {
  local label=$1 label_link block sys_path parent

  UD_FAST_DEVICE=
  UD_FAST_PARENT=
  UD_FAST_DISKSEQ=
  label_link="/dev/disk/by-label/$label"
  [[ -d /dev/disk/by-label && -e "$label_link" ]] || return 1

  UD_FAST_DEVICE=$(/usr/bin/readlink -f -- "$label_link") || return 1
  [[ -b "$UD_FAST_DEVICE" ]] || return 1
  block=${UD_FAST_DEVICE##*/}
  [[ -e "/sys/class/block/$block" ]] || return 1

  if [[ -f "/sys/class/block/$block/partition" ]]; then
    sys_path=$(/usr/bin/readlink -f -- "/sys/class/block/$block") || return 1
    parent=${sys_path%/*}
    parent=${parent##*/}
  else
    parent=$block
  fi
  [[ -e "/sys/class/block/$parent/diskseq" ]] || return 1

  UD_FAST_PARENT="/dev/$parent"
  IFS= read -r UD_FAST_DISKSEQ < "/sys/class/block/$parent/diskseq" || return 1
  [[ -n "$UD_FAST_DISKSEQ" ]]
}

ud_remove_spindown_state() {
  local label=$1 marker="$UD_STATE_DIR/$label"
  if [[ -e "$marker" ]]; then
    /usr/bin/rm -f -- "$marker" || return 1
  fi
  return 0
}

ud_clear_spindown_state() {
  local label
  if [[ -e "$UD_STATE_DIR" && ! -d "$UD_STATE_DIR" ]]; then
    ud_record_failure "$UD_STATE_DIR exists but is not a directory"
    return 1
  fi
  for label in "${HDD_LABELS[@]}"; do
    ud_remove_spindown_state "$label" || return 1
  done
  [[ -d "$UD_STATE_DIR" ]] && /usr/bin/rmdir -- "$UD_STATE_DIR" 2>/dev/null || true
}

ud_spindown_state_is_current() {
  local label=$1 marker="$UD_STATE_DIR/$label" stored mounts rc
  [[ -f "$marker" ]] || return 1
  ud_fast_identity "$label" || {
    ud_remove_spindown_state "$label"
    return 1
  }
  IFS= read -r stored < "$marker" || {
    ud_remove_spindown_state "$label"
    return 1
  }
  if [[ "$stored" != "$UD_FAST_DEVICE $UD_FAST_PARENT $UD_FAST_DISKSEQ" ]]; then
    ud_remove_spindown_state "$label"
    return 1
  fi

  # A manual remount invalidates the state even if the hardware is unchanged.
  mounts=$(/usr/bin/findmnt -rn -S "$UD_FAST_DEVICE" -o TARGET 2>&1)
  rc=$?
  if (( rc == 1 )) && [[ -z "$mounts" ]]; then
    return 0
  fi
  ud_remove_spindown_state "$label"
  return 1
}

ud_write_spindown_state() {
  local label=$1 expected_device=$2 expected_parent=$3 marker tmp
  ud_fast_identity "$label" || {
    ud_record_failure "cannot build non-probing spindown identity for $label"
    return 1
  }
  if [[ "$UD_FAST_DEVICE" != "$expected_device" || "$UD_FAST_PARENT" != "$expected_parent" ]]; then
    ud_record_failure "$label device identity changed while recording spindown state"
    return 1
  fi

  if [[ -e "$UD_STATE_DIR" && ! -d "$UD_STATE_DIR" ]]; then
    ud_record_failure "$UD_STATE_DIR exists but is not a directory"
    return 1
  fi
  /usr/bin/install -d -m 700 "$UD_STATE_DIR" || {
    ud_record_failure "cannot create $UD_STATE_DIR"
    return 1
  }
  marker="$UD_STATE_DIR/$label"
  tmp="$UD_STATE_DIR/.$label.$$"
  if ! (umask 077; printf '%s %s %s\n' \
      "$UD_FAST_DEVICE" "$UD_FAST_PARENT" "$UD_FAST_DISKSEQ" > "$tmp"); then
    ud_record_failure "cannot write spindown state for $label"
    return 1
  fi
  if ! /usr/bin/mv -f -- "$tmp" "$marker"; then
    /usr/bin/rm -f -- "$tmp"
    ud_record_failure "cannot install spindown state for $label"
    return 1
  fi
}

ud_record_failure() {
  local message=$1
  echo "ERROR: $message" >&2
  UD_FAILURES+=("$message")
}

ud_is_hdd_label() {
  local wanted=$1 label
  for label in "${HDD_LABELS[@]}"; do
    [[ "$label" == "$wanted" ]] && return 0
  done
  return 1
}

# Set UD_PARENT to the one whole-disk ancestor of a block device.
ud_find_parent_disk() {
  local device=$1 ancestry name type extra
  local -a parents=()

  ancestry=$(/usr/bin/lsblk -s -nrpo NAME,TYPE "$device" 2>&1) || {
    ud_record_failure "lsblk failed for $device: $ancestry"
    return 1
  }
  while read -r name type extra; do
    [[ "$type" == "disk" ]] && parents+=("$name")
  done <<< "$ancestry"

  if (( ${#parents[@]} != 1 )); then
    ud_record_failure "$device has ${#parents[@]} whole-disk ancestors; expected exactly one"
    return 1
  fi
  UD_PARENT=${parents[0]}
}

# Resolve an exact filesystem label and validate its physical parent.
# Returns 0 when present, 1 when safely absent, and 2 on a discovery error.
ud_resolve_label() {
  local label=$1 label_link query rc actual_label transport root_source root_parent
  local -a devices=()

  UD_DEVICE=
  UD_PARENT=
  UD_MOUNTS=

  label_link="/dev/disk/by-label/$label"
  if [[ ! -d /dev/disk/by-label ]]; then
    ud_record_failure "/dev/disk/by-label is unavailable; refusing disk discovery"
    return 2
  elif [[ ! -e "$label_link" ]]; then
    return 1
  fi

  query=$(/usr/bin/sudo /sbin/blkid -t "LABEL=$label" -o device 2>&1)
  rc=$?
  if (( rc == 2 )) && [[ -z "$query" ]]; then
    ud_record_failure "udev reports label $label at $label_link but blkid found no matching filesystem"
    return 2
  elif (( rc != 0 )); then
    ud_record_failure "blkid lookup for label $label failed (status $rc): $query"
    return 2
  fi

  while IFS= read -r device; do
    [[ -n "$device" ]] && devices+=("$device")
  done <<< "$query"
  if (( ${#devices[@]} != 1 )); then
    ud_record_failure "label $label resolved to ${#devices[@]} devices; expected exactly one"
    return 2
  fi

  UD_DEVICE=$(/usr/bin/readlink -f -- "${devices[0]}") || {
    ud_record_failure "cannot resolve ${devices[0]} for label $label"
    return 2
  }
  if [[ ! -b "$UD_DEVICE" ]]; then
    ud_record_failure "label $label resolved to non-block device $UD_DEVICE"
    return 2
  fi

  actual_label=$(/usr/bin/sudo /sbin/blkid -s LABEL -o value "$UD_DEVICE" 2>&1) || {
    ud_record_failure "cannot verify label on $UD_DEVICE: $actual_label"
    return 2
  }
  if [[ "$actual_label" != "$label" ]]; then
    ud_record_failure "$UD_DEVICE label changed during discovery (expected $label, got $actual_label)"
    return 2
  fi

  ud_find_parent_disk "$UD_DEVICE" || return 2
  transport=$(/usr/bin/lsblk -dnro TRAN "$UD_PARENT" 2>&1) || {
    ud_record_failure "cannot determine transport for $UD_PARENT: $transport"
    return 2
  }
  if [[ "$transport" != "usb" ]]; then
    ud_record_failure "refusing label $label on non-USB parent $UD_PARENT (transport=${transport:-unknown})"
    return 2
  fi

  root_source=$(/usr/bin/findmnt -nro SOURCE / 2>&1) || {
    ud_record_failure "cannot identify the root filesystem source: $root_source"
    return 2
  }
  root_source=$(/usr/bin/readlink -f -- "$root_source") || {
    ud_record_failure "cannot resolve root filesystem source $root_source"
    return 2
  }
  ud_find_parent_disk "$root_source" || return 2
  root_parent=$UD_PARENT
  ud_find_parent_disk "$UD_DEVICE" || return 2
  if [[ "$UD_PARENT" == "$root_parent" ]]; then
    ud_record_failure "refusing label $label because $UD_PARENT contains the root filesystem"
    return 2
  fi

  UD_MOUNTS=$(/usr/bin/findmnt -rn -S "$UD_DEVICE" -o TARGET 2>&1)
  rc=$?
  if (( rc == 1 )) && [[ -z "$UD_MOUNTS" ]]; then
    UD_MOUNTS=
  elif (( rc != 0 )); then
    ud_record_failure "findmnt failed for $UD_DEVICE (status $rc): $UD_MOUNTS"
    return 2
  fi
  return 0
}

# Return success only when no partition on a parent disk is mounted.
ud_parent_is_unmounted() {
  local parent=$1 nodes rc node mounts

  nodes=$(/usr/bin/lsblk -nrpo NAME "$parent" 2>&1) || {
    ud_record_failure "cannot enumerate block devices below $parent: $nodes"
    return 1
  }
  while IFS= read -r node; do
    [[ -n "$node" ]] || continue
    mounts=$(/usr/bin/findmnt -rn -S "$node" -o TARGET 2>&1)
    rc=$?
    if (( rc == 0 )); then
      ud_record_failure "refusing to spin down $parent: $node is still mounted at $mounts"
      return 1
    elif (( rc != 1 )); then
      ud_record_failure "findmnt failed while checking $node (status $rc): $mounts"
      return 1
    fi
  done <<< "$nodes"
  return 0
}

ud_kill_torrent_client() {
  local rc attempt
  /usr/bin/pgrep -f '[q]bittorrent-nox' >/dev/null 2>&1
  rc=$?
  (( rc == 1 )) && return 0
  (( rc == 0 )) || return 1

  echo "asking qbittorrent-nox to stop"
  /usr/bin/sudo /usr/bin/pkill -TERM -f '[q]bittorrent-nox'
  rc=$?
  (( rc <= 1 )) || return 1

  # This Pi's qBittorrent can need about 18 seconds to save state and exit.
  # Keep the stop graceful, but bound ignition shutdown at 30 seconds.
  for attempt in {1..30}; do
    /usr/bin/pgrep -f '[q]bittorrent-nox' >/dev/null 2>&1
    rc=$?
    (( rc == 1 )) && return 0
    (( rc == 0 )) || return 1
    sleep 1
  done
  return 1
}

ud_notify_failures() {
  local should_notify=$1 summary
  (( should_notify )) || return 0
  (( ${#UD_FAILURES[@]} )) || return 0
  summary=$(IFS='; '; echo "${UD_FAILURES[*]}")
  /home/pi/scripts/ntfy_send.sh \
    "vanpi HDD shutdown failed" "$summary" high warning || true
}

umount_disks_main() {
  local dry_run=0 spindown=0 clear_state=0 explicit_label= label arg rc expected_mount target
  local post_mounts
  local parent_name hd_idle_output
  local needs_torrent_stop=0 had_preflight_failure=0 had_runtime_failure=0
  local -a labels=() attached_labels=() mounted_labels=()
  local -A devices=() parents=() mounts=() unmount_failed=() spun_parents=()

  while (( $# )); do
    arg=$1
    shift
    case "$arg" in
      --dry-run) dry_run=1 ;;
      --spindown) spindown=1 ;;
      --clear-spindown-state) clear_state=1 ;;
      --help|-h) ud_usage; return 0 ;;
      --*) ud_usage; return 2 ;;
      *)
        if [[ -n "$explicit_label" ]]; then
          ud_usage
          return 2
        fi
        explicit_label=$arg
        ;;
    esac
  done

  if (( clear_state )); then
    if (( dry_run || spindown )) || [[ -n "$explicit_label" ]]; then
      ud_usage
      return 2
    fi
    ud_clear_spindown_state
    return $?
  fi

  if [[ -n "$explicit_label" ]]; then
    labels=("$explicit_label")
    if (( spindown )) && ! ud_is_hdd_label "$explicit_label"; then
      ud_record_failure "refusing to spin down label $explicit_label because it is not in HDD_LABELS"
      ud_notify_failures "$(( spindown && ! dry_run ))"
      return 1
    fi
  else
    labels=("${HDD_LABELS[@]}")
  fi

  # Complete all discovery and mountpoint validation before changing anything.
  for label in "${labels[@]}"; do
    if (( spindown )) && ud_spindown_state_is_current "$label"; then
      echo "$label: already stopped on $UD_FAST_PARENT; skipping disk probe"
      continue
    fi

    ud_resolve_label "$label"
    rc=$?
    if (( rc == 1 )); then
      echo "$label: not attached"
      continue
    elif (( rc != 0 )); then
      had_preflight_failure=1
      continue
    fi

    devices[$label]=$UD_DEVICE
    parents[$label]=$UD_PARENT
    mounts[$label]=$UD_MOUNTS
    attached_labels+=("$label")
    expected_mount="/mnt/$label"

    if [[ -n "$UD_MOUNTS" ]]; then
      while IFS= read -r target; do
        [[ -n "$target" ]] || continue
        if [[ "$target" != "$expected_mount" ]]; then
          ud_record_failure "$label is mounted at unexpected target $target (expected $expected_mount)"
          had_preflight_failure=1
        fi
      done <<< "$UD_MOUNTS"
      mounted_labels+=("$label")
      [[ "$label" == "movingparts" ]] && needs_torrent_stop=1
    fi
  done

  if (( had_preflight_failure )); then
    echo "refusing all disk changes because preflight validation failed" >&2
    ud_notify_failures "$(( spindown && ! dry_run ))"
    return 1
  fi

  if (( spindown && ${#attached_labels[@]} )) && [[ ! -x /usr/sbin/hd-idle ]]; then
    ud_record_failure "/usr/sbin/hd-idle is unavailable; refusing ignition disk shutdown"
    ud_notify_failures "$(( spindown && ! dry_run ))"
    return 1
  fi

  for label in "${attached_labels[@]}"; do
    if [[ -n "${mounts[$label]}" ]]; then
      echo "$label: ${devices[$label]} -> ${mounts[$label]}; parent=${parents[$label]}; action=unmount$([[ $spindown == 1 ]] && echo '+spindown')"
    else
      echo "$label: ${devices[$label]} is unmounted; parent=${parents[$label]}; action=$([[ $spindown == 1 ]] && echo spindown || echo none)"
    fi
  done
  if (( dry_run )); then
    echo "dry run: no services, mounts, or disks were changed"
    return 0
  fi

  if (( ${#mounted_labels[@]} )); then
    if ! /usr/bin/sudo /home/pi/scripts/backup/abort_backup.sh; then
      ud_record_failure "backup/restore did not stop; refusing to unmount HDDs"
      ud_notify_failures "$(( spindown && ! dry_run ))"
      return 1
    fi

    if (( needs_torrent_stop )) && ! ud_kill_torrent_client; then
      ud_record_failure "qbittorrent-nox did not stop; refusing to unmount HDDs"
      ud_notify_failures "$(( spindown && ! dry_run ))"
      return 1
    fi

    if ! /usr/bin/sudo /usr/sbin/service smbd stop; then
      ud_record_failure "smbd did not stop; refusing to unmount HDDs"
      ud_notify_failures "$(( spindown && ! dry_run ))"
      return 1
    fi
  fi

  for label in "${mounted_labels[@]}"; do
    expected_mount="/mnt/$label"
    echo "unmounting $label from $expected_mount"
    if ! /usr/bin/sudo /usr/bin/umount -- "$expected_mount"; then
      ud_record_failure "normal unmount failed for $label at $expected_mount"
      unmount_failed[$label]=1
      had_runtime_failure=1
      continue
    fi

    post_mounts=$(/usr/bin/findmnt -rn -S "${devices[$label]}" -o TARGET 2>&1)
    rc=$?
    if (( rc == 0 )); then
      ud_record_failure "$label still has a mount after umount returned success"
      unmount_failed[$label]=1
      had_runtime_failure=1
    elif (( rc != 1 )); then
      ud_record_failure "cannot verify $label after unmount (findmnt status $rc): $post_mounts"
      unmount_failed[$label]=1
      had_runtime_failure=1
    fi
  done

  if (( spindown )); then
    for label in "${attached_labels[@]}"; do
      [[ -z "${unmount_failed[$label]:-}" ]] || continue

      # Re-resolve immediately before the destructive device command and ensure
      # USB enumeration did not change under us.
      ud_resolve_label "$label"
      rc=$?
      if (( rc != 0 )); then
        [[ $rc == 1 ]] && ud_record_failure "$label disappeared before spindown"
        had_runtime_failure=1
        continue
      fi
      if [[ "$UD_DEVICE" != "${devices[$label]}" || "$UD_PARENT" != "${parents[$label]}" ]]; then
        ud_record_failure "$label device mapping changed before spindown; refusing it"
        had_runtime_failure=1
        continue
      fi
      if [[ -n "$UD_MOUNTS" ]]; then
        ud_record_failure "$label was remounted at $UD_MOUNTS before spindown"
        had_runtime_failure=1
        continue
      fi
      [[ -z "${spun_parents[$UD_PARENT]:-}" ]] || continue
      if ! ud_parent_is_unmounted "$UD_PARENT"; then
        had_runtime_failure=1
        continue
      fi

      # Debian Bookworm's hd-idle 1.05 prepends /dev/ to -t internally and can
      # return zero even when it printed an open error, so pass only the kernel
      # disk name and require both a zero status and no diagnostic output.
      parent_name=${UD_PARENT##*/}
      echo "spinning down $label on $UD_PARENT"
      hd_idle_output=$(/usr/bin/sudo /usr/sbin/hd-idle -t "$parent_name" 2>&1)
      rc=$?
      if (( rc == 0 )) && [[ -z "$hd_idle_output" ]]; then
        spun_parents[$UD_PARENT]=1
        if ! ud_write_spindown_state "$label" "$UD_DEVICE" "$UD_PARENT"; then
          had_runtime_failure=1
        fi
      else
        ud_record_failure "hd-idle failed for $label on $UD_PARENT (status $rc): ${hd_idle_output:-no diagnostic output}"
        had_runtime_failure=1
      fi
    done
  fi

  echo "mounted USB/SCSI filesystems after disk operation:"
  /usr/bin/findmnt -rn -t ext4,exfat,hfsplus -o SOURCE,TARGET | /usr/bin/grep '^/dev/sd' || true

  ud_notify_failures "$(( spindown && ! dry_run ))"
  (( had_runtime_failure == 0 ))
}

umount_disks_main "$@"
umount_disks_rc=$?
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  exit "$umount_disks_rc"
else
  return "$umount_disks_rc"
fi
