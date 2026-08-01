#!/bin/bash
# Repair an always-mounted exFAT volume only after two consecutive checks show
# that pi can neither read nor write its mounted filesystem.
set -u

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=disk_policy.sh
. "$script_dir/disk_policy.sh" || exit 1

diskctl=${DISK_HEALTH_WATCH_DISKCTL:-"$script_dir/diskctl"}
findmnt_command=${DISK_HEALTH_WATCH_FINDMNT:-/usr/bin/findmnt}
blkid_command=${DISK_HEALTH_WATCH_BLKID:-/sbin/blkid}
readlink_command=${DISK_HEALTH_WATCH_READLINK:-/usr/bin/readlink}
sudo_command=${DISK_HEALTH_WATCH_SUDO:-/usr/bin/sudo}
timeout_command=${DISK_HEALTH_WATCH_TIMEOUT:-/usr/bin/timeout}
find_command=${DISK_HEALTH_WATCH_FIND:-/usr/bin/find}
touch_command=${DISK_HEALTH_WATCH_TOUCH:-/usr/bin/touch}
rm_command=${DISK_HEALTH_WATCH_RM:-/usr/bin/rm}
install_command=${DISK_HEALTH_WATCH_INSTALL:-/usr/bin/install}
mv_command=${DISK_HEALTH_WATCH_MV:-/usr/bin/mv}
date_command=${DISK_HEALTH_WATCH_DATE:-/usr/bin/date}
state_dir=${DISK_HEALTH_WATCH_STATE_DIR:-/run/lock/vanpi-disk-health-watch}
mount_root=${DISK_HEALTH_WATCH_MOUNT_ROOT:-/mnt}
ignition_flag=${DISK_HEALTH_WATCH_IGNITION_FLAG:-/home/pi/hooks/ignition_is_on}
cooldown_seconds=${DISK_HEALTH_WATCH_COOLDOWN_SECONDS:-900}

[[ "$cooldown_seconds" =~ ^[1-9][0-9]{0,4}$ ]] || {
  echo "ERROR: invalid disk-health repair cooldown: $cooldown_seconds" >&2
  exit 2
}

dhw_prepare_state_dir() {
  if [[ -L "$state_dir" || ( -e "$state_dir" && ! -d "$state_dir" ) ]]; then
    echo "ERROR: unsafe disk-health watchdog state directory: $state_dir" >&2
    return 1
  fi
  "$install_command" -d -m 0700 -- "$state_dir"
}

dhw_clear_state() {
  local label=$1
  "$rm_command" -f -- "$state_dir/$label.unusable" "$state_dir/$label.cooldown"
}

dhw_write_state() {
  local path=$1 contents=$2 temporary
  temporary="$path.$$"
  if ! (umask 077; printf '%s\n' "$contents" > "$temporary") ||
     ! "$mv_command" -f -- "$temporary" "$path"; then
    "$rm_command" -f -- "$temporary"
    return 1
  fi
}

# Set DHW_DEVICE only when the exact mount source, filesystem label, and
# filesystem type all agree. Return 1 for an ordinary absent/unmounted label.
dhw_resolve_mounted_exfat() {
  local label=$1 target=$2 output status device actual_label actual_type

  DHW_DEVICE=
  output=$("$findmnt_command" -rn -M "$target" -o SOURCE 2>&1)
  status=$?
  if (( status == 1 )) && [[ -z "$output" ]]; then
    return 1
  elif (( status != 0 )) || [[ -z "$output" || "$output" == *$'\n'* ]]; then
    echo "ERROR: cannot identify the exact mount source for $label at $target" >&2
    return 2
  fi
  device=$("$readlink_command" -f -- "$output") || return 2

  # Probe only the already-mounted source. A token-only blkid label lookup
  # scans unrelated block devices and can wake a deliberately spun-down disk.
  # On the RTL9201/UAS path that broad probe has escalated through SCSI error
  # recovery and killed the Pi 4's entire VL805 USB controller. The exact
  # source plus label/type checks retain the repair identity guard without
  # touching sleeping devices.
  actual_label=$(
    "$sudo_command" "$blkid_command" -s LABEL -o value -- "$device"
  ) || {
    echo "ERROR: cannot verify the label on mounted source $device" >&2
    return 2
  }
  actual_type=$(
    "$sudo_command" "$blkid_command" -s TYPE -o value -- "$device"
  ) || {
    echo "ERROR: cannot verify the filesystem type on mounted source $device" >&2
    return 2
  }
  if [[ "$actual_label" != "$label" ]]; then
    echo "ERROR: $target is mounted from $device with label '$actual_label', not '$label'" >&2
    return 2
  fi
  if [[ "$actual_type" != exfat ]]; then
    # Automated repair is intentionally narrower than dashboard reporting.
    return 1
  fi
  DHW_DEVICE=$device
}

dhw_check_label() {
  local label=$1 target marker cooldown now stored_device first_seen
  local deadline read_status write_status probe status

  target="$mount_root/$label"
  marker="$state_dir/$label.unusable"
  cooldown="$state_dir/$label.cooldown"
  now=$("$date_command" +%s) || return 1
  [[ "$now" =~ ^[1-9][0-9]{0,10}$ ]] || return 1

  if [[ -e "$ignition_flag" ]]; then
    dhw_clear_state "$label"
    return 0
  fi

  dhw_resolve_mounted_exfat "$label" "$target"
  status=$?
  if (( status == 1 )); then
    dhw_clear_state "$label"
    return 0
  elif (( status != 0 )); then
    "$rm_command" -f -- "$marker"
    return 1
  fi

  disk_health_is_quarantined "$label"
  status=$?
  if (( status == 0 )); then
    echo "$label is quarantined after a failed check; automatic repair will not retry"
    "$rm_command" -f -- "$marker"
    return 0
  elif (( status != 1 )); then
    return 1
  fi

  if [[ -e "$cooldown" ]]; then
    if [[ -L "$cooldown" || ! -f "$cooldown" ]]; then
      echo "ERROR: unsafe repair cooldown state for $label" >&2
      return 1
    fi
    IFS= read -r deadline < "$cooldown" || deadline=
    if [[ "$deadline" =~ ^[1-9][0-9]{0,10}$ ]] && (( now < deadline )); then
      return 0
    fi
    "$rm_command" -f -- "$cooldown"
  fi

  "$sudo_command" -u pi "$timeout_command" 5 "$find_command" \
    "$target" -mindepth 1 -maxdepth 1 -print -quit >/dev/null 2>&1
  read_status=$?

  probe="$target/.vanpi_health_watch_probe.$$"
  "$sudo_command" -u pi "$timeout_command" 5 "$touch_command" "$probe" \
    >/dev/null 2>&1
  write_status=$?
  if (( write_status == 0 )); then
    "$sudo_command" -u pi "$rm_command" -f -- "$probe" >/dev/null 2>&1 || {
      echo "WARNING: could not remove disk-health probe $probe" >&2
      return 1
    }
  fi

  # A readable read-only mount remains useful and is left for an explicit
  # dashboard repair. Automatic repair is only for a wholly unusable mount.
  if (( read_status == 0 || write_status == 0 )); then
    dhw_clear_state "$label"
    return 0
  fi

  if [[ ! -e "$marker" ]]; then
    echo "$label is mounted but neither readable nor writable; awaiting confirmation"
    dhw_write_state "$marker" "$DHW_DEVICE $now"
    return $?
  fi
  if [[ -L "$marker" || ! -f "$marker" ]]; then
    echo "ERROR: unsafe unusable-mount state for $label" >&2
    return 1
  fi
  read -r stored_device first_seen < "$marker" || {
    "$rm_command" -f -- "$marker"
    return 1
  }
  if [[ "$stored_device" != "$DHW_DEVICE" ||
        ! "$first_seen" =~ ^[1-9][0-9]{0,10}$ ]]; then
    dhw_write_state "$marker" "$DHW_DEVICE $now"
    return $?
  fi

  echo "$label remained neither readable nor writable; starting exact-label repair"
  if "$diskctl" repair "$label"; then
    dhw_clear_state "$label"
    echo "$label automatic repair completed"
    return 0
  fi

  deadline=$((now + cooldown_seconds))
  dhw_write_state "$cooldown" "$deadline" || true
  "$rm_command" -f -- "$marker"
  echo "ERROR: automatic repair failed for $label; retry suppressed for $cooldown_seconds seconds" >&2
  return 1
}

dhw_prepare_state_dir || exit 1
had_failure=0
for label in "${ALWAYS_MOUNT_LABELS[@]}"; do
  dhw_check_label "$label" || had_failure=1
done
exit "$had_failure"
