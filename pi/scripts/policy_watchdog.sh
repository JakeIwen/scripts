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

now=$(/usr/bin/date +%s) || exit 1
started=
if [[ -f "$ISW_OWNER_FILE" ]]; then
  read -r owner_pid started < "$ISW_OWNER_FILE" || started=
fi
if [[ ! "${owner_pid:-}" =~ ^[0-9]+$ ]] ||
   ! kill -0 "$owner_pid" 2>/dev/null ||
   [[ ! "$started" =~ ^[0-9]+$ ]]; then
  started=$(/usr/bin/stat -c %Y -- "$ISW_LOCK_FILE" 2>/dev/null) || {
    echo "policy lock is held but its start time is unavailable" >&2
    exit 1
  }
fi

age=$((now - started))
(( age >= POLICY_STALL_SECONDS )) || exit 0

echo "policy reconciliation lock has been held for ${age}s"
isw_notify_lock_timeout
