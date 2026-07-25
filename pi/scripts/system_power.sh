#!/bin/bash
# Shared fail-closed implementation for safe_reboot.sh and safe_power_down.sh.
set -u

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
unmount_disks=${SAFE_SYSTEM_POWER_UNMOUNT_DISKS:-"$script_dir/umount_disks.sh"}
sync_command=${SAFE_SYSTEM_POWER_SYNC:-/usr/bin/sync}
sudo_command=${SAFE_SYSTEM_POWER_SUDO:-/usr/bin/sudo}
systemctl_command=${SAFE_SYSTEM_POWER_SYSTEMCTL:-/usr/bin/systemctl}
flock_command=${SAFE_SYSTEM_POWER_FLOCK:-/usr/bin/flock}
lifecycle_lock=${SAFE_SYSTEM_POWER_LIFECYCLE_LOCK:-/home/pi/.internet_switches.lock}
lock_wait=${SAFE_SYSTEM_POWER_LOCK_WAIT:-55}

usage() {
  echo "usage: ${0##*/} reboot|poweroff" >&2
}

action=${1:-}
[[ $# == 1 && ( "$action" == reboot || "$action" == poweroff ) ]] || {
  usage
  exit 2
}
[[ "$lock_wait" =~ ^[1-9][0-9]{0,2}$ ]] || {
  echo "ERROR: invalid lifecycle lock wait: $lock_wait" >&2
  exit 2
}

for required in "$unmount_disks" "$sync_command" "$sudo_command" \
  "$systemctl_command" "$flock_command"; do
  [[ -x "$required" ]] || {
    echo "ERROR: required command is not executable: $required" >&2
    exit 1
  }
done

# Automatic policy, ignition hooks, and dashboard disk actions all use this
# lock. Hold it until systemd accepts the final action so nothing can remount a
# disk between the verified unmount and the reboot/poweroff request.
exec 9>"$lifecycle_lock" || {
  echo "ERROR: cannot open disk lifecycle lock: $lifecycle_lock" >&2
  exit 1
}
if ! "$flock_command" -w "$lock_wait" 9; then
  echo "ERROR: disk lifecycle is busy; refusing to $action" >&2
  exit 1
fi

echo "preparing disk-safe $action"
if ! "$unmount_disks" --all; then
  echo "ERROR: disk unmount or verification failed; refusing to $action" >&2
  exit 1
fi
if ! "$sync_command"; then
  echo "ERROR: final filesystem sync failed; refusing to $action" >&2
  exit 1
fi

echo "managed disks are unmounted and filesystems are synced; requesting $action"
if ! "$sudo_command" "$systemctl_command" "$action"; then
  echo "ERROR: systemd rejected $action; disks remain safely unmounted" >&2
  exit 1
fi
