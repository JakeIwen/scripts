#!/bin/bash
# Filesystem/partition labels that mount_disks.sh reconciles automatically.
# ALWAYS_MOUNT_LABELS remain mounted independently; the other labels follow
# requested HDD policy. This is also the allowlist for stale-mount recovery.
MOUNT_LABELS=(
  movingparts
  mbp2tbkup
  EXFAT512
)

# Automatically mounted labels that remain available regardless of ignition or
# the rotational-disk policy. Keep this as a subset of MOUNT_LABELS so existing
# stale-mount recovery and dashboard eject holds apply unchanged.
ALWAYS_MOUNT_LABELS=(
  EXFAT512
)

# Filesystem/partition labels that may be mounted and unmounted explicitly but
# are not part of automatic policy reconciliation. This lets backup disks stay
# unmounted after a backup while remaining available to dashboard/user tools.
MANUAL_MOUNT_LABELS=(
  bigboi
  hdd1tb
)

# Filesystem labels for rotational USB disks that must be unmounted and
# explicitly spun down while the van is running.  A newly attached disk is
# intentionally ignored until its label is added here.
HDD_LABELS=(
  movingparts
  bigboi
  mbp2tbkup
  hdd1tb
)

# Static Samba share names for disk-backed exports.  Keep this mapping aligned
# with configs/smb.conf.  Labels without a Samba export deliberately return 1.
disk_policy_samba_share_name() {
  case "$1" in
    mbp2tbkup) printf '%s\n' mbp2tbkup ;;
    movingparts) printf '%s\n' MovingParts ;;
    bigboi) printf '%s\n' BigBoi ;;
    EXFAT512) printf '%s\n' EXFAT512 ;;
    *) return 1 ;;
  esac
}

# A dashboard/user eject is intentionally temporary: automatic policy
# reconciliation skips that exact label until its absolute deadline. Runtime
# state lives under /run so a reboot always clears the hold.
DISK_EJECT_HOLD_DIR=${DISK_EJECT_HOLD_DIR:-/run/lock/vanpi-disk-eject}
DISK_HEALTH_STATE_DIR=${DISK_HEALTH_STATE_DIR:-/var/lib/vanpi-disk-health}

# Exact-label discovery must not use `blkid -t LABEL=...`: a token lookup can
# probe every block device and wake (or block on) unrelated disks that policy
# deliberately spun down.  Discover candidates from udev's non-probing label
# links, then ask blkid to verify only the selected device.  Callers may
# override these paths for tests; production defaults remain explicit.
DISK_POLICY_BY_LABEL_DIR=${DISK_POLICY_BY_LABEL_DIR:-/dev/disk/by-label}
DISK_POLICY_BY_PARTLABEL_DIR=${DISK_POLICY_BY_PARTLABEL_DIR:-/dev/disk/by-partlabel}
DISK_POLICY_READLINK=${DISK_POLICY_READLINK:-/usr/bin/readlink}
DISK_POLICY_BLKID=${DISK_POLICY_BLKID:-/sbin/blkid}
DISK_POLICY_SUDO=${DISK_POLICY_SUDO:-/usr/bin/sudo}
DISK_POLICY_UDEVADM=${DISK_POLICY_UDEVADM:-/usr/bin/udevadm}
DISK_POLICY_SYS_BLOCK_DIR=${DISK_POLICY_SYS_BLOCK_DIR:-/sys/class/block}
DISK_POLICY_REQUIRE_BLOCK_DEVICE=${DISK_POLICY_REQUIRE_BLOCK_DEVICE:-1}
DISK_POLICY_RESOLVED_DEVICE=
DISK_POLICY_RESOLVED_LABEL_KEY=
DISK_POLICY_RESOLVE_ERROR=

# Resolve a filesystem LABEL, or a LABEL/PARTLABEL when requested. Returns 0
# for one verified device, 1 when no udev mapping exists, and 2 for an unsafe
# or unverifiable mapping. Results and diagnostics are returned in the globals
# above so callers can preserve their own logging/error conventions.
disk_policy_resolve_exact_label() {
  local label=${1:-} mode=${2:-label-only}
  local namespace directory path key resolved output rc index found sys_path
  local property value devname fs_label partlabel
  local -a namespaces=() devices=() keys=() udev_devices=()
  local -a sys_paths=()

  DISK_POLICY_RESOLVED_DEVICE=
  DISK_POLICY_RESOLVED_LABEL_KEY=
  DISK_POLICY_RESOLVE_ERROR=

  if [[ -z "$label" || "$label" == */* || "$label" == . || "$label" == .. ]]; then
    DISK_POLICY_RESOLVE_ERROR="invalid disk label"
    return 2
  fi
  if [[ "$DISK_POLICY_REQUIRE_BLOCK_DEVICE" != 0 &&
        "$DISK_POLICY_REQUIRE_BLOCK_DEVICE" != 1 ]]; then
    DISK_POLICY_RESOLVE_ERROR="invalid block-device requirement"
    return 2
  fi
  case "$mode" in
    label-only) namespaces=(LABEL) ;;
    label-or-partlabel) namespaces=(LABEL PARTLABEL) ;;
    *)
      DISK_POLICY_RESOLVE_ERROR="invalid label-resolution mode: $mode"
      return 2
      ;;
  esac

  for namespace in "${namespaces[@]}"; do
    case "$namespace" in
      LABEL) directory=$DISK_POLICY_BY_LABEL_DIR ;;
      PARTLABEL) directory=$DISK_POLICY_BY_PARTLABEL_DIR ;;
    esac
    [[ -d "$directory" ]] || continue
    path="$directory/$label"
    if [[ ! -e "$path" && ! -L "$path" ]]; then
      continue
    elif [[ ! -L "$path" ]]; then
      DISK_POLICY_RESOLVE_ERROR="$path is not a udev symlink"
      return 2
    fi

    output=$("$DISK_POLICY_READLINK" -f -- "$path" 2>&1)
    rc=$?
    if (( rc != 0 )) || [[ -z "$output" ]]; then
      DISK_POLICY_RESOLVE_ERROR="cannot resolve udev $namespace mapping $path: ${output:-readlink status $rc}"
      return 2
    fi
    resolved=$output
    if (( DISK_POLICY_REQUIRE_BLOCK_DEVICE )) && [[ ! -b "$resolved" ]]; then
      DISK_POLICY_RESOLVE_ERROR="udev $namespace mapping $path resolved to non-block device $resolved"
      return 2
    fi

    found=-1
    for index in "${!devices[@]}"; do
      if [[ "${devices[$index]}" == "$resolved" ]]; then
        found=$index
        break
      fi
    done
    if (( found >= 0 )); then
      # Prefer the filesystem label when one device legitimately carries both.
      [[ "$namespace" == LABEL ]] && keys[$found]=LABEL
    else
      devices+=("$resolved")
      keys+=("$namespace")
    fi
  done

  if [[ ! -d "$DISK_POLICY_SYS_BLOCK_DIR" || ! -x "$DISK_POLICY_UDEVADM" ]]; then
    DISK_POLICY_RESOLVE_ERROR="udev block-device database is unavailable"
    return 2
  fi
  sys_paths=("$DISK_POLICY_SYS_BLOCK_DIR"/*)
  if (( ${#sys_paths[@]} == 0 )) || [[ ! -e "${sys_paths[0]}" ]]; then
    DISK_POLICY_RESOLVE_ERROR="no block devices are available in $DISK_POLICY_SYS_BLOCK_DIR"
    return 2
  fi

  # The label symlink namespace can represent only one target for a given
  # name. Cross-check every live sysfs block device against udev's cached
  # properties so duplicate labels still fail closed without probing media.
  for sys_path in "${sys_paths[@]}"; do
    output=$("$DISK_POLICY_UDEVADM" info --query=property --path="$sys_path" 2>&1)
    rc=$?
    if (( rc != 0 )); then
      DISK_POLICY_RESOLVE_ERROR="cannot query udev properties for $sys_path (status $rc): $output"
      return 2
    fi
    devname=
    fs_label=
    partlabel=
    while IFS='=' read -r property value; do
      case "$property" in
        DEVNAME) devname=$value ;;
        ID_FS_LABEL) fs_label=$value ;;
        ID_PART_ENTRY_NAME) partlabel=$value ;;
      esac
    done <<< "$output"

    if [[ "$fs_label" == "$label" ]]; then
      :
    elif [[ "$mode" == label-or-partlabel && "$partlabel" == "$label" ]]; then
      :
    else
      continue
    fi
    if [[ -z "$devname" ]]; then
      DISK_POLICY_RESOLVE_ERROR="udev matched $label at $sys_path without a DEVNAME"
      return 2
    fi
    output=$("$DISK_POLICY_READLINK" -f -- "$devname" 2>&1)
    rc=$?
    if (( rc != 0 )) || [[ -z "$output" ]]; then
      DISK_POLICY_RESOLVE_ERROR="cannot resolve udev device $devname for label $label: ${output:-readlink status $rc}"
      return 2
    fi
    resolved=$output
    if (( DISK_POLICY_REQUIRE_BLOCK_DEVICE )) && [[ ! -b "$resolved" ]]; then
      DISK_POLICY_RESOLVE_ERROR="udev device $devname for label $label is not a block device"
      return 2
    fi

    found=-1
    for index in "${!udev_devices[@]}"; do
      if [[ "${udev_devices[$index]}" == "$resolved" ]]; then
        found=$index
        break
      fi
    done
    if (( found < 0 )); then
      udev_devices+=("$resolved")
    fi
  done

  if (( ${#devices[@]} == 0 && ${#udev_devices[@]} == 0 )); then
    return 1
  elif (( ${#devices[@]} == 0 )); then
    DISK_POLICY_RESOLVE_ERROR="udev database reports exact label $label without a matching label symlink"
    return 2
  elif (( ${#devices[@]} != 1 )); then
    DISK_POLICY_RESOLVE_ERROR="exact label $label has multiple udev mappings: ${devices[*]}"
    return 2
  elif (( ${#udev_devices[@]} == 0 )); then
    DISK_POLICY_RESOLVE_ERROR="udev label link for $label has no matching live database record"
    return 2
  elif (( ${#udev_devices[@]} != 1 )); then
    DISK_POLICY_RESOLVE_ERROR="exact label $label matches ${#udev_devices[@]} live udev devices: ${udev_devices[*]}"
    return 2
  elif [[ "${devices[0]}" != "${udev_devices[0]}" ]]; then
    DISK_POLICY_RESOLVE_ERROR="udev link and database disagree for $label (${devices[0]} vs ${udev_devices[0]})"
    return 2
  fi

  resolved=${devices[0]}
  key=${keys[0]}
  output=$("$DISK_POLICY_SUDO" "$DISK_POLICY_BLKID" \
    -s "$key" -o value -- "$resolved" 2>&1)
  rc=$?
  if (( rc != 0 )); then
    DISK_POLICY_RESOLVE_ERROR="cannot verify $key on $resolved (status $rc): $output"
    return 2
  elif [[ "$output" != "$label" ]]; then
    DISK_POLICY_RESOLVE_ERROR="$resolved $key changed during discovery (expected $label, got ${output:-empty})"
    return 2
  fi

  DISK_POLICY_RESOLVED_DEVICE=$resolved
  DISK_POLICY_RESOLVED_LABEL_KEY=$key
  return 0
}

disk_policy_is_mount_label() {
  local wanted=$1 label
  for label in "${MOUNT_LABELS[@]}"; do
    [[ "$label" == "$wanted" ]] && return 0
  done
  return 1
}

disk_policy_is_always_mount_label() {
  local wanted=$1 label
  for label in "${ALWAYS_MOUNT_LABELS[@]}"; do
    [[ "$label" == "$wanted" ]] && return 0
  done
  return 1
}

disk_policy_is_manual_mount_label() {
  local wanted=$1 label
  for label in "${MANUAL_MOUNT_LABELS[@]}"; do
    [[ "$label" == "$wanted" ]] && return 0
  done
  return 1
}

disk_policy_is_control_label() {
  disk_policy_is_mount_label "$1" || disk_policy_is_manual_mount_label "$1"
}

disk_eject_now_epoch() {
  if [[ -n ${DISK_EJECT_NOW:-} ]]; then
    printf '%s\n' "$DISK_EJECT_NOW"
  else
    /usr/bin/date +%s
  fi
}

disk_eject_prepare_dir() {
  if [[ -L "$DISK_EJECT_HOLD_DIR" ||
        ( -e "$DISK_EJECT_HOLD_DIR" && ! -d "$DISK_EJECT_HOLD_DIR" ) ]]; then
    echo "ERROR: unsafe disk eject hold directory: $DISK_EJECT_HOLD_DIR" >&2
    return 1
  fi
  /usr/bin/install -d -m 700 -- "$DISK_EJECT_HOLD_DIR"
}

disk_eject_hold_set() {
  local label=$1 seconds=$2 now deadline marker temporary
  disk_policy_is_mount_label "$label" || {
    echo "ERROR: disk label '$label' is not managed for automatic mounting" >&2
    return 1
  }
  [[ "$seconds" =~ ^[1-9][0-9]{0,2}$ ]] && ((seconds <= 300)) || {
    echo "ERROR: disk eject hold must be from 1 to 300 seconds" >&2
    return 1
  }
  now=$(disk_eject_now_epoch) || return 1
  [[ "$now" =~ ^[1-9][0-9]{0,10}$ ]] || return 1
  deadline=$((now + seconds))
  ((deadline > now)) || return 1
  disk_eject_prepare_dir || return 1
  marker="$DISK_EJECT_HOLD_DIR/$label"
  temporary="$DISK_EJECT_HOLD_DIR/.$label.$$"
  if ! (umask 077; printf '%s\n' "$deadline" > "$temporary") ||
     ! /bin/mv -f -- "$temporary" "$marker"; then
    /bin/rm -f -- "$temporary"
    return 1
  fi
  printf '%s\n' "$deadline"
}

# Print seconds remaining. Return 0 for a live hold, 1 for no/expired hold,
# and 2 for malformed state. Malformed state is removed, but the current mount
# pass still fails closed; the next minutely pass can proceed normally.
disk_eject_hold_remaining() {
  local label=$1 marker deadline now
  disk_policy_is_mount_label "$label" || return 1
  marker="$DISK_EJECT_HOLD_DIR/$label"
  [[ -e "$marker" ]] || return 1
  if [[ -L "$marker" || ! -f "$marker" ]]; then
    echo "ERROR: unsafe eject hold marker for $label" >&2
    return 2
  fi
  IFS= read -r deadline < "$marker" || true
  if [[ ! "$deadline" =~ ^[1-9][0-9]{0,10}$ ]]; then
    /bin/rm -f -- "$marker" || true
    echo "ERROR: malformed eject hold marker for $label" >&2
    return 2
  fi
  now=$(disk_eject_now_epoch) || return 2
  [[ "$now" =~ ^[1-9][0-9]{0,10}$ ]] || return 2
  if ((now >= deadline)); then
    /bin/rm -f -- "$marker" || return 2
    return 1
  fi
  printf '%s\n' "$((deadline - now))"
}

disk_eject_hold_clear() {
  local label=$1 marker
  disk_policy_is_mount_label "$label" || return 1
  marker="$DISK_EJECT_HOLD_DIR/$label"
  [[ ! -e "$marker" && ! -L "$marker" ]] || /bin/rm -f -- "$marker"
}

disk_health_quarantine_marker() {
  printf '%s/quarantine/%s\n' "$DISK_HEALTH_STATE_DIR" "$1"
}

disk_health_is_quarantined() {
  local label=$1 marker
  disk_policy_is_control_label "$label" || return 2
  marker=$(disk_health_quarantine_marker "$label") || return 2
  if [[ -L "$DISK_HEALTH_STATE_DIR" ||
        -L "$DISK_HEALTH_STATE_DIR/quarantine" ||
        -L "$marker" ]]; then
    echo "ERROR: unsafe disk-health quarantine state for $label" >&2
    return 2
  fi
  [[ -e "$marker" ]]
}
