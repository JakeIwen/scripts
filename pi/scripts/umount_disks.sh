#!/bin/bash

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=disk_policy.sh
if ! . "$script_dir/disk_policy.sh"; then
  echo "ERROR: cannot load $script_dir/disk_policy.sh" >&2
  return 1 2>/dev/null || exit 1
fi
samba_share_control=${UMOUNT_DISKS_SAMBA_SHARE_CONTROL:-"$script_dir/samba_share_control.sh"}

UD_DEVICE=
UD_PARENT=
UD_MOUNTS=
UD_FAST_DEVICE=
UD_FAST_PARENT=
UD_FAST_DISKSEQ=
UD_FAILURES=()
UD_STATE_DIR=/run/lock/vanpi-hdd-spindown
UD_QBIT_GRACE_SECONDS=${UMOUNT_DISKS_QBIT_GRACE_SECONDS:-30}
UD_EMERGENCY_QBIT_GRACE_SECONDS=${UMOUNT_DISKS_EMERGENCY_QBIT_GRACE_SECONDS:-8}

ud_usage() {
  echo "usage: ${0##*/} [--dry-run] [--spindown] [label]" >&2
  echo "       ${0##*/} --spindown --emergency" >&2
  echo "       ${0##*/} [--dry-run] --all" >&2
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
  local label=$1 rc transport root_source root_parent

  UD_DEVICE=
  UD_PARENT=
  UD_MOUNTS=

  if [[ ! -d "$DISK_POLICY_BY_LABEL_DIR" ]]; then
    ud_record_failure "$DISK_POLICY_BY_LABEL_DIR is unavailable; refusing disk discovery"
    return 2
  fi

  disk_policy_resolve_exact_label "$label" label-only
  rc=$?
  if (( rc == 1 )); then
    return 1
  elif (( rc != 0 )); then
    # A dead USB controller can leave a by-label link pointing at a vanished
    # /dev node. --all may handle this one classified result after proving no
    # mount remains; every other caller and discovery failure stays fail-closed.
    if [[ "$DISK_POLICY_RESOLVE_REASON" != vanished-udev-mapping ]]; then
      ud_record_failure "$DISK_POLICY_RESOLVE_ERROR"
    fi
    return 2
  fi
  UD_DEVICE=$DISK_POLICY_RESOLVED_DEVICE

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

# A vanished udev label link is safe to ignore only for the all-filesystem
# reboot/poweroff preflight, and only when neither its expected target nor the
# vanished source appears anywhere in the kernel mount table. Returns 0 for
# that narrow detached state and 1 after recording any mounted/unknown state.
ud_accept_vanished_label_for_all() {
  local label=$1 device=$2 expected_mount
  local source output rc

  expected_mount="/mnt/$label"

  if [[ "$DISK_POLICY_RESOLVE_REASON" != vanished-udev-mapping ||
        -z "$device" || "$device" != /dev/* || -e "$device" || -b "$device" ]]; then
    ud_record_failure "$DISK_POLICY_RESOLVE_ERROR"
    return 1
  fi

  output=$(ud_findmnt_exact_source "$expected_mount" 2>&1)
  rc=$?
  if (( rc == 0 )); then
    source=$(printf '%s\n' "$output" | /usr/bin/awk 'NF { print; exit }')
    ud_record_failure "$DISK_POLICY_RESOLVE_ERROR; $expected_mount remains mounted from ${source:-an unknown source}"
    return 1
  elif (( rc != 1 )) || [[ -n "$output" ]]; then
    ud_record_failure "$DISK_POLICY_RESOLVE_ERROR; cannot verify $expected_mount is unmounted (findmnt status $rc): ${output:-no diagnostic output}"
    return 1
  fi

  output=$(ud_findmnt_source_targets "$device" 2>&1)
  rc=$?
  if (( rc == 0 )); then
    ud_record_failure "$DISK_POLICY_RESOLVE_ERROR; vanished source $device remains mounted at $output"
    return 1
  elif (( rc != 1 )) || [[ -n "$output" ]]; then
    ud_record_failure "$DISK_POLICY_RESOLVE_ERROR; cannot verify vanished source $device is unmounted (findmnt status $rc): ${output:-no diagnostic output}"
    return 1
  fi

  echo "$label: udev mapping points to vanished $device and no mount remains; treating as detached for --all"
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

ud_qbit_is_running() {
  /usr/bin/pgrep -x qbittorrent-nox >/dev/null 2>&1
}

ud_signal_qbit() {
  local signal=$1
  [[ "$signal" == TERM || "$signal" == KILL ]] || return 2
  /usr/bin/sudo /usr/bin/pkill "-$signal" -x qbittorrent-nox
}

ud_qbit_wait_one_second() {
  sleep 1
}

ud_kill_torrent_client() {
  local emergency=${1:-0} rc attempt wait_seconds=$UD_QBIT_GRACE_SECONDS

  if (( emergency )); then
    wait_seconds=$UD_EMERGENCY_QBIT_GRACE_SECONDS
  fi
  [[ "$wait_seconds" =~ ^[1-9][0-9]?$ ]] || {
    echo "ERROR: invalid qBittorrent stop timeout: $wait_seconds" >&2
    return 1
  }

  ud_qbit_is_running
  rc=$?
  (( rc == 1 )) && return 0
  (( rc == 0 )) || return 1

  echo "asking qbittorrent-nox to stop"
  ud_signal_qbit TERM
  rc=$?
  (( rc <= 1 )) || return 1

  # This Pi's qBittorrent can need about 18 seconds to save state and exit.
  # Normal disk shutdown retains that grace period and ignition emergency mode
  # uses a shorter deadline. Either path then kills only the exact executable
  # name because leaving a managed filesystem mounted is the greater risk.
  for ((attempt = 0; attempt < wait_seconds; attempt++)); do
    ud_qbit_is_running
    rc=$?
    (( rc == 1 )) && return 0
    (( rc == 0 )) || return 1
    ud_qbit_wait_one_second
  done

  echo "qbittorrent-nox did not stop within ${wait_seconds}s; killing it"
  ud_signal_qbit KILL
  rc=$?
  (( rc <= 1 )) || return 1
  for attempt in {1..3}; do
    ud_qbit_is_running
    rc=$?
    (( rc == 1 )) && return 0
    (( rc == 0 )) || return 1
    ud_qbit_wait_one_second
  done
  return 1
}

ud_mount_holder_summary() {
  local mountpoint=$1 output rc summary

  output=$(
    /usr/bin/timeout 3 \
      /usr/bin/sudo /usr/bin/fuser -vmM "$mountpoint" 2>&1
  )
  rc=$?
  if (( rc == 124 )); then
    printf '%s\n' "userspace-holder scan timed out"
    return 0
  elif (( rc != 0 && rc != 1 )); then
    printf '%s\n' "userspace-holder scan failed (fuser status $rc)"
    return 0
  fi

  # fuser always reports the kernel's own mount entry. Keep only userspace
  # rows, which contain the useful user, PID, access mode, and process name.
  summary=$(
    /usr/bin/awk '
      NR == 1 || /kernel[[:space:]]+mount/ { next }
      NF {
        sub(/^[[:space:]]+/, "")
        printf "%s%s", separator, $0
        separator = "; "
      }
      END { if (separator != "") print "" }
    ' <<< "$output"
  )
  printf '%s\n' "${summary:-no userspace mount holder identified}"
}

ud_sync_mount() {
  local mountpoint=$1 output rc

  output=$(
    /usr/bin/sudo /usr/bin/timeout --kill-after=2 8 \
      /usr/bin/sync -f -- "$mountpoint" 2>&1
  )
  rc=$?
  if (( rc != 0 )); then
    echo "WARNING: bounded sync failed for $mountpoint (status $rc): ${output:-no diagnostic output}" >&2
    return 1
  fi
}

ud_normal_unmount() {
  /usr/bin/sudo /usr/bin/timeout --kill-after=2 10 \
    /usr/bin/umount -- "$1"
}

ud_findmnt_source_targets() {
  /usr/bin/findmnt -rn -S "$1" -o TARGET
}

ud_findmnt_exact_source() {
  /usr/bin/findmnt -rn -M "$1" -o SOURCE
}

# umount can remain blocked in userspace long enough for timeout(1) to return
# 124 even though the kernel has already completed the unmount. Reconcile a
# nonzero command status against the exact source device before escalating or
# reporting failure. Only findmnt's unambiguous "not found" result is success;
# mounted devices and discovery errors continue to fail closed.
ud_reconcile_unmount_result() {
  local label=$1 device=$2 command_rc=$3 targets findmnt_rc
  (( command_rc != 0 )) || return 0

  targets=$(ud_findmnt_source_targets "$device" 2>&1)
  findmnt_rc=$?
  if (( findmnt_rc == 1 )) && [[ -z "$targets" ]]; then
    echo "umount returned status $command_rc for $label, but findmnt verifies $device is unmounted; continuing"
    return 0
  fi
  if (( findmnt_rc != 0 && findmnt_rc != 1 )); then
    echo "WARNING: cannot reconcile unmount result for $label (findmnt status $findmnt_rc): ${targets:-no diagnostic output}" >&2
  elif (( findmnt_rc == 1 )); then
    echo "WARNING: ambiguous findmnt result while reconciling $label: $targets" >&2
  fi
  return 1
}

ud_signal_mount_holders() {
  local mountpoint=$1 signal=$2
  [[ "$signal" == TERM || "$signal" == KILL ]] || return 2
  /usr/bin/sudo /usr/bin/timeout --kill-after=1 3 \
    /usr/bin/fuser -k "-$signal" -mM "$mountpoint"
}

ud_holder_wait() {
  sleep "$1"
}

ud_evict_mount_holders() {
  local mountpoint=$1 before after output rc

  before=$(ud_mount_holder_summary "$mountpoint")
  echo "holder eviction scan for $mountpoint: $before"
  output=$(ud_signal_mount_holders "$mountpoint" TERM 2>&1)
  rc=$?
  if (( rc != 0 && rc != 1 )); then
    echo "WARNING: TERM holder eviction failed for $mountpoint (status $rc): ${output:-no diagnostic output}" >&2
  fi

  ud_holder_wait 2
  after=$(ud_mount_holder_summary "$mountpoint")
  if [[ "$after" == "no userspace mount holder identified" ]]; then
    return 0
  fi

  echo "holders remain for $mountpoint after TERM: $after"
  output=$(ud_signal_mount_holders "$mountpoint" KILL 2>&1)
  rc=$?
  if (( rc != 0 && rc != 1 )); then
    echo "WARNING: KILL holder eviction failed for $mountpoint (status $rc): ${output:-no diagnostic output}" >&2
  fi
  ud_holder_wait 1
}

ud_emergency_stop_samba() {
  local output rc attempt

  echo "share-scoped Samba closure failed; stopping smbd globally"
  output=$(
    /usr/bin/sudo /usr/bin/timeout --kill-after=2 8 \
      /usr/bin/systemctl stop smbd.service 2>&1
  )
  rc=$?
  if (( rc != 0 )); then
    echo "WARNING: graceful smbd stop failed (status $rc): ${output:-no diagnostic output}" >&2
  fi

  for attempt in {1..3}; do
    /usr/bin/pgrep -x smbd >/dev/null 2>&1
    rc=$?
    (( rc == 1 )) && return 0
    (( rc == 0 )) || return 1
    sleep 1
  done

  echo "smbd processes remain; killing the smbd service cgroup"
  /usr/bin/sudo /usr/bin/systemctl kill --kill-who=all --signal=KILL smbd.service \
    >/dev/null 2>&1 || true
  /usr/bin/sudo /usr/bin/pkill -KILL -x smbd >/dev/null 2>&1 || true
  sleep 1
  /usr/bin/pgrep -x smbd >/dev/null 2>&1
  rc=$?
  (( rc == 1 ))
}

ud_notify_failures() {
  local should_notify=$1 summary
  (( should_notify )) || return 0
  (( ${#UD_FAILURES[@]} )) || return 0
  summary=$(IFS='; '; echo "${UD_FAILURES[*]}")
  /home/pi/scripts/hdd_alert.sh failure "$summary" || true
}

ud_notify_recovery() {
  local should_notify=$1
  (( should_notify )) || return 0
  /home/pi/scripts/hdd_alert.sh recovery || true
}

umount_disks_main() {
  local dry_run=0 spindown=0 emergency=0 clear_state=0 all_labels=0 explicit_label=
  local label arg rc expected_mount target
  local post_mounts all_mounts remaining_scsi_mounts
  local parent_name hd_idle_output
  local share samba_names samba_output samba_detail line holder_summary
  local drain_output unmount_output first_holders final_holders
  local needs_torrent_stop=0 had_preflight_failure=0 had_runtime_failure=0
  local -a labels=() attached_labels=() mounted_labels=() samba_shares=()
  local -a abort_args=()
  local -A devices=() parents=() mounts=() unmount_failed=() spun_parents=()

  while (( $# )); do
    arg=$1
    shift
    case "$arg" in
      --dry-run) dry_run=1 ;;
      --spindown) spindown=1 ;;
      --emergency) emergency=1 ;;
      --all) all_labels=1 ;;
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
    if (( dry_run || spindown || emergency || all_labels )) || [[ -n "$explicit_label" ]]; then
      ud_usage
      return 2
    fi
    ud_clear_spindown_state
    return $?
  fi

  if (( all_labels )) && { (( spindown )) || [[ -n "$explicit_label" ]]; }; then
    ud_usage
    return 2
  fi
  if (( emergency )) &&
      { (( ! spindown || dry_run || all_labels )) || [[ -n "$explicit_label" ]]; }; then
    ud_usage
    return 2
  fi

  if (( all_labels )); then
    # One preflight covers every filesystem this repository is permitted to
    # mount. This is used by safe reboot/poweroff so no earlier per-label
    # unmount can be undone before a later label is validated.
    labels=("${MOUNT_LABELS[@]}" "${MANUAL_MOUNT_LABELS[@]}")
  elif [[ -n "$explicit_label" ]]; then
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
      if (( all_labels )) &&
          [[ "$DISK_POLICY_RESOLVE_REASON" == vanished-udev-mapping ]]; then
        if ud_accept_vanished_label_for_all \
            "$label" "$DISK_POLICY_VANISHED_DEVICE"; then
          continue
        fi
      elif [[ "$DISK_POLICY_RESOLVE_REASON" == vanished-udev-mapping ]]; then
        ud_record_failure "$DISK_POLICY_RESOLVE_ERROR"
      fi
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
    for label in "${mounted_labels[@]}"; do
      if share=$(disk_policy_samba_share_name "$label"); then
        samba_shares+=("$share")
      fi
    done
    samba_names=${samba_shares[0]:-none}
    for share in "${samba_shares[@]:1}"; do
      samba_names+=", $share"
    done

    # Block Finder and other clients from reconnecting between close-share and
    # the actual unmount. The marker remains until mount_disks explicitly
    # accepts the mounted filesystem again.
    drain_output=$("$samba_share_control" drain "${mounted_labels[@]}" 2>&1)
    rc=$?
    [[ -z "$drain_output" ]] || printf '%s\n' "$drain_output"
    if (( rc != 0 )); then
      ud_record_failure "cannot drain Samba shares $samba_names; refusing to unmount disks"
      ud_notify_failures "$(( spindown && ! dry_run ))"
      return 1
    fi

    (( emergency )) && abort_args=(--emergency)
    if ! /usr/bin/sudo /home/pi/scripts/backup/abort_backup.sh "${abort_args[@]}"; then
      ud_record_failure "backup/restore did not stop; refusing to unmount HDDs"
      ud_notify_failures "$(( spindown && ! dry_run ))"
      return 1
    fi

    if (( needs_torrent_stop )) && ! ud_kill_torrent_client "$emergency"; then
      echo "WARNING: qbittorrent-nox stop could not be verified; continuing with guarded unmount and exact holder eviction" >&2
    fi

    # Release handles only for the drained shares. In ignition emergency mode,
    # a failed scoped close escalates to stopping Samba globally.
    samba_output=$("$samba_share_control" close "${mounted_labels[@]}" 2>&1)
    rc=$?
    [[ -z "$samba_output" ]] || printf '%s\n' "$samba_output"
    if (( rc != 0 )); then
      if (( emergency )) && ud_emergency_stop_samba; then
        echo "all smbd processes stopped; continuing emergency disk shutdown"
      else
        samba_detail=
        while IFS= read -r line; do
          if [[ "$line" == "ERROR: "* ]]; then
            line=${line#ERROR: }
            samba_detail+="${samba_detail:+; }$line"
          fi
        done <<< "$samba_output"
        if [[ -n "$samba_detail" ]]; then
          ud_record_failure "$samba_detail; disks remain mounted"
        else
          ud_record_failure "Samba shares $samba_names did not close; disks remain mounted"
        fi
        ud_notify_failures "$(( spindown && ! dry_run ))"
        return 1
      fi
    fi
  fi

  for label in "${mounted_labels[@]}"; do
    expected_mount="/mnt/$label"
    first_holders=
    final_holders=
    ud_sync_mount "$expected_mount" || true
    echo "unmounting $label from $expected_mount"
    unmount_output=$(ud_normal_unmount "$expected_mount" 2>&1)
    rc=$?
    if (( rc != 0 )) &&
        ud_reconcile_unmount_result "$label" "${devices[$label]}" "$rc"; then
      rc=0
    fi
    # Once preflight, backup termination, and share draining have succeeded,
    # preserving a userspace process is not a reason to leave a managed disk
    # mounted. Evict only holders of this exact mount, then retry a normal
    # unmount so the kernel remains the authority on whether it is safe.
    if (( rc != 0 )); then
      first_holders=$(ud_mount_holder_summary "$expected_mount")
      echo "normal unmount failed for $label (status $rc): ${unmount_output:-no diagnostic output}"
      ud_evict_mount_holders "$expected_mount" || true
      ud_sync_mount "$expected_mount" || true
      echo "retrying normal unmount for $label after holder eviction"
      unmount_output=$(ud_normal_unmount "$expected_mount" 2>&1)
      rc=$?
      if (( rc != 0 )) &&
          ud_reconcile_unmount_result "$label" "${devices[$label]}" "$rc"; then
        rc=0
      fi
    fi
    if (( rc != 0 )); then
      final_holders=$(ud_mount_holder_summary "$expected_mount")
      holder_summary=${first_holders:-$final_holders}
      [[ "$final_holders" == "$holder_summary" ]] ||
        holder_summary+="; remaining after escalation: $final_holders"
      ud_record_failure \
        "normal unmount failed for $label at $expected_mount (status $rc): ${unmount_output:-no diagnostic output}; holders: $holder_summary"
      unmount_failed[$label]=1
      had_runtime_failure=1
      continue
    fi

    post_mounts=$(ud_findmnt_source_targets "${devices[$label]}" 2>&1)
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
        if [[ "$DISK_POLICY_RESOLVE_REASON" == vanished-udev-mapping ]]; then
          ud_record_failure "$DISK_POLICY_RESOLVE_ERROR"
        fi
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

  if (( all_labels && ! dry_run )); then
    # A mounted, unrecognised USB/SCSI filesystem must not silently survive a
    # request advertised as disk-safe. Do not dynamically unmount unknown
    # devices: fail closed so their identity and consumers can be reviewed.
    all_mounts=$(/usr/bin/findmnt -rn -o SOURCE,TARGET 2>&1)
    rc=$?
    if (( rc != 0 )); then
      ud_record_failure "cannot verify mounted filesystems after --all (findmnt status $rc): $all_mounts"
      had_runtime_failure=1
    else
      remaining_scsi_mounts=$(
        /usr/bin/grep '^/dev/sd' <<< "$all_mounts" || true
      )
      if [[ -n "$remaining_scsi_mounts" ]]; then
        ud_record_failure "USB/SCSI filesystems remain mounted after --all: $remaining_scsi_mounts"
        had_runtime_failure=1
      fi
    fi
  fi

  echo "mounted USB/SCSI filesystems after disk operation:"
  /usr/bin/findmnt -rn -t ext4,exfat,hfsplus -o SOURCE,TARGET | /usr/bin/grep '^/dev/sd' || true

  if (( had_runtime_failure )); then
    ud_notify_failures "$(( spindown && ! dry_run ))"
  else
    ud_notify_recovery "$(( spindown && ! dry_run ))"
  fi
  (( had_runtime_failure == 0 ))
}

if [[ "${UMOUNT_DISKS_LIBRARY_ONLY:-0}" != 1 ]]; then
  umount_disks_main "$@"
  umount_disks_rc=$?
  if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    exit "$umount_disks_rc"
  else
    return "$umount_disks_rc"
  fi
fi
