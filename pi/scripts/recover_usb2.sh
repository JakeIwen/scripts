#!/bin/bash
# Recover the Raspberry Pi 4's internal USB 2 hub without resetting the VL805
# PCIe controller. This intentionally leaves the independent USB 3 bus alone.

set -euo pipefail

readonly UHUBCTL=/usr/sbin/uhubctl
readonly SUDO=/usr/bin/sudo
readonly FINDMNT=/usr/bin/findmnt
readonly READLINK=/usr/bin/readlink
readonly FLOCK=/usr/bin/flock
readonly SLEEP=/usr/bin/sleep
readonly TIMEOUT=/usr/bin/timeout
readonly EXPECTED_HUB_VENDOR=2109
readonly EXPECTED_HUB_PRODUCT=3431
readonly HUB_LOCATION=1
readonly HUB_PORT=1
readonly LOCK_PATH=/run/lock/vanpi-usb2-recovery.lock

fail() {
  echo "USB 2 recovery refused: $*" >&2
  exit 1
}

for command_path in "$UHUBCTL" "$SUDO" "$FINDMNT" "$READLINK" "$FLOCK" "$SLEEP" "$TIMEOUT"; do
  [[ -x "$command_path" ]] || fail "required command is unavailable: $command_path"
done

exec 9>"$LOCK_PATH"
"$FLOCK" -n 9 || fail "another USB 2 recovery is already running"

# The Pi 4 exposes every physical USB 2 socket beneath the internal VIA hub at
# 1-1. Fail closed if this is not the expected hardware/topology.
[[ -r /sys/bus/usb/devices/1-1/idVendor ]] || fail "internal USB 2 hub is not present at 1-1"
[[ -r /sys/bus/usb/devices/1-1/idProduct ]] || fail "internal USB 2 hub identity is unavailable"
[[ "$(< /sys/bus/usb/devices/1-1/idVendor)" == "$EXPECTED_HUB_VENDOR" ]] || \
  fail "unexpected device at USB location 1-1"
[[ "$(< /sys/bus/usb/devices/1-1/idProduct)" == "$EXPECTED_HUB_PRODUCT" ]] || \
  fail "unexpected device at USB location 1-1"

# Cycling 1-1 disconnects every USB 2 socket. Never do that while storage on
# that path is mounted, even if a future dashboard caller misses the condition.
mounted_targets=()
for block_path in /sys/class/block/*; do
  [[ -e "$block_path" ]] || continue
  if ! resolved_path="$($READLINK -f -- "$block_path")"; then
    fail "could not resolve block-device topology for ${block_path##*/}"
  fi
  case "$resolved_path" in
    */usb1/1-1/*)
      source_path="/dev/${block_path##*/}"
      findmnt_rc=0
      mount_targets="$($FINDMNT -rn -S "$source_path" -o TARGET 2>/dev/null)" || findmnt_rc=$?
      if (( findmnt_rc > 1 )); then
        fail "could not verify mount state for $source_path"
      fi
      if [[ -n "$mount_targets" ]]; then
        mounted_targets+=("$source_path ($mount_targets)")
      fi
      ;;
  esac
done
if (( ${#mounted_targets[@]} )); then
  fail "mounted USB 2 storage is present: ${mounted_targets[*]}"
fi

status="$($TIMEOUT 10 "$SUDO" -n "$UHUBCTL" -l "$HUB_LOCATION" -p "$HUB_PORT")" || \
  fail "could not inspect the internal USB 2 hub port"
if ! /usr/bin/grep -Eq 'Port 1:.*connect \[2109:3431' <<<"$status"; then
  fail "internal USB 2 hub is not connected to the expected root port"
fi

echo "Cycling the Pi internal USB 2 hub; USB 3 remains online..."
if ! "$TIMEOUT" 20 "$SUDO" -n "$UHUBCTL" -l "$HUB_LOCATION" -p "$HUB_PORT" -a cycle -d 3; then
  # Best effort only: do not leave the hub off after a partial uhubctl failure.
  "$TIMEOUT" 10 "$SUDO" -n "$UHUBCTL" -l "$HUB_LOCATION" -p "$HUB_PORT" -a on \
    >/dev/null 2>&1 || true
  fail "the internal hub power cycle failed"
fi

# Enumeration is asynchronous. Verify the fixed internal hub identity before
# reporting success; downstream devices may continue appearing for a moment.
for _attempt in {1..20}; do
  if [[ -r /sys/bus/usb/devices/1-1/idVendor && -r /sys/bus/usb/devices/1-1/idProduct ]] &&
     [[ "$(< /sys/bus/usb/devices/1-1/idVendor)" == "$EXPECTED_HUB_VENDOR" ]] &&
     [[ "$(< /sys/bus/usb/devices/1-1/idProduct)" == "$EXPECTED_HUB_PRODUCT" ]]; then
    echo "USB 2 hub restored; downstream devices are re-enumerating."
    exit 0
  fi
  "$SLEEP" 0.5
done

fail "the internal USB 2 hub did not return after the power cycle"
