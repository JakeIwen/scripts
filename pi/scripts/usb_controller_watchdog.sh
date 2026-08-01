#!/bin/bash
# Alert once per boot when the Pi 4 VL805 xHCI controller is declared dead.
# Recovery is deliberately not automatic: resetting/removing the live PCIe
# controller has deadlocked this host before, and a reboot must first pass the
# repository's disk-safe unmount checks.
set -u

journalctl_command=${USB_CONTROLLER_WATCH_JOURNALCTL:-/usr/bin/journalctl}
notify_command=${USB_CONTROLLER_WATCH_NOTIFY:-/home/pi/scripts/ntfy_send.sh}
install_command=${USB_CONTROLLER_WATCH_INSTALL:-/usr/bin/install}
mv_command=${USB_CONTROLLER_WATCH_MV:-/bin/mv}
rm_command=${USB_CONTROLLER_WATCH_RM:-/bin/rm}
date_command=${USB_CONTROLLER_WATCH_DATE:-/usr/bin/date}
state_dir=${USB_CONTROLLER_WATCH_STATE_DIR:-/run/lock/vanpi-usb-controller-watch}
dead_pattern='xHCI host controller not responding, assume dead'

for required in "$journalctl_command" "$notify_command" "$install_command" \
  "$mv_command" "$rm_command" "$date_command"; do
  [[ -x "$required" ]] || {
    echo "ERROR: required USB-controller watchdog command is unavailable: $required" >&2
    exit 1
  }
done

events=$("$journalctl_command" -k -b --no-pager -g "$dead_pattern" 2>&1)
status=$?
if (( status == 1 )); then
  exit 0
elif (( status != 0 )); then
  echo "ERROR: cannot inspect the current boot for xHCI controller death (status $status): $events" >&2
  exit 1
fi
[[ "$events" == *"$dead_pattern"* ]] || exit 0

if [[ -L "$state_dir" || ( -e "$state_dir" && ! -d "$state_dir" ) ]]; then
  echo "ERROR: unsafe USB-controller watchdog state directory: $state_dir" >&2
  exit 1
fi
"$install_command" -d -m 0700 -- "$state_dir" || exit 1

marker="$state_dir/alerted"
if [[ -e "$marker" || -L "$marker" ]]; then
  if [[ -f "$marker" && ! -L "$marker" ]]; then
    exit 0
  fi
  echo "ERROR: unsafe USB-controller alert marker: $marker" >&2
  exit 1
fi

message="The Pi 4 VL805 xHCI controller was declared dead; all downstream USB devices are unavailable. Do not remove/rescan the PCIe controller. Run sudo /home/pi/scripts/safe_reboot.sh; if USB remains absent, shut down and fully power-cycle the Pi and powered hub."
if ! "$notify_command" "vanpi USB controller DEAD" "$message" urgent rotating_light; then
  echo "ERROR: USB-controller death detected, but the alert could not be delivered" >&2
  exit 1
fi

temporary="$state_dir/.alerted.$$"
if ! (umask 077; "$date_command" +%s > "$temporary") ||
   ! "$mv_command" -f -- "$temporary" "$marker"; then
  "$rm_command" -f -- "$temporary"
  echo "ERROR: USB-controller alert was sent, but its rate-limit marker could not be recorded" >&2
  exit 1
fi

echo "USB-controller death alert sent; automatic reset/reboot is intentionally disabled"
