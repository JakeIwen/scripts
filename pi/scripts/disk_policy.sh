#!/bin/bash
# Filesystem/partition labels that mount_disks.sh reconciles automatically.
# ALWAYS_MOUNT_LABELS remain mounted independently; the other labels follow
# requested HDD policy. This is also the allowlist for stale-mount recovery.
MOUNT_LABELS=(
  movingparts
  mbp1tbkup
  mbp2tbkup
  hfs2tb
  usbext
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
)

# Filesystem labels for rotational USB disks that must be unmounted and
# explicitly spun down while the van is running.  A newly attached disk is
# intentionally ignored until its label is added here.
HDD_LABELS=(
  movingparts
  bigboi
  mbp2tbkup
)

# Static Samba share names for disk-backed exports.  Keep this mapping aligned
# with configs/smb.conf.  Labels without a Samba export deliberately return 1.
disk_policy_samba_share_name() {
  case "$1" in
    mbp1tbkup) printf '%s\n' mbp1tbkup ;;
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
