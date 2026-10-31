#!/bin/bash
# Alert when internet_switches.sh holds its lifecycle lock abnormally long.
# This stays independent of vanpi-policy.service because systemd coalesces a
# second start request while the oneshot is already running.
set -u

POLICY_STALL_SECONDS=${POLICY_STALL_SECONDS:-120}
[[ "$POLICY_STALL_SECONDS" =~ ^[1-9][0-9]*$ ]] || {
  echo "invalid POLICY_STALL_SECONDS: $POLICY_STALL_SECONDS" >&2
  exit 2
}

ISW_SCRIPT=${ISW_SCRIPT:-/home/pi/scripts/internet_switches.sh}
[[ -r "$ISW_SCRIPT" ]] || {
  echo "cannot read policy reconciler: $ISW_SCRIPT" >&2
  exit 1
}
# shellcheck source=internet_switches.sh
. "$ISW_SCRIPT"

[[ -e "$ISW_LOCK_FILE" && ! -L "$ISW_LOCK_FILE" ]] || exit 0
exec 9<"$ISW_LOCK_FILE" || {
  echo "cannot inspect policy lock: $ISW_LOCK_FILE" >&2
  exit 1
}
if /usr/bin/flock -n 9; then
  /usr/bin/flock -u 9 || true
  exit 0
fi

owner_pid=
started=
if [[ -f "$ISW_OWNER_FILE" ]]; then
  read -r owner_pid started < "$ISW_OWNER_FILE" || started=
fi
if [[ "$owner_pid" =~ ^[0-9]+$ ]] && kill -0 "$owner_pid" 2>/dev/null &&
   [[ "$started" =~ ^[0-9]+$ ]]; then
  now=$(/usr/bin/date +%s) || exit 1
  age=$((now - started))
  (( age >= POLICY_STALL_SECONDS )) || exit 0
  held="${age}s"
else
  # Only internet_switches records an owner. diskctl, system_power,
  # exfat_snapshot and the metering reconciler (briefly, every 10s tick) take
  # the same lock without one, and the lock file's mtime is only the last
  # write-open, not this holder's start. Measure the hold directly instead.
  if /usr/bin/flock -w "$POLICY_STALL_SECONDS" 9; then
    /usr/bin/flock -u 9 || true
    exit 0
  fi
  held="at least ${POLICY_STALL_SECONDS}s"
fi

echo "policy reconciliation lock has been held for $held"
isw_notify_lock_timeout "The vanpi lifecycle lock (shared by internet_switches, diskctl, system power, the EXFAT snapshot and metering) has been held for $held."
