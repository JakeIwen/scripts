#!/bin/bash
# Exit 0 when ignition_on.sh may turn ext_flood off; exit 1 when a persisted
# active COP ALERT must retain control.  The engine marker is created only
# from fresh passive C-CAN RPM evidence by van_dashboard.py.
set -u

runtime_dir="${VAN_DASHBOARD_RUNTIME_DIR:-/run/van-dashboard}"
state_path="${VAN_DASHBOARD_STATE_PATH:-/home/pi/.van_dashboard_state.json}"
active_marker="$runtime_dir/cop-alert.active"
engine_marker="$runtime_dir/engine-running"
engine_max_age=3

fresh_regular_file() {
  local path="$1"
  local max_age="$2"
  local modified now age

  [[ -f "$path" && ! -L "$path" ]] || return 1
  modified=$(/usr/bin/stat -c %Y -- "$path") || return 1
  now=$(/usr/bin/date +%s) || return 1
  age=$((now - modified))
  (( age >= 0 && age <= max_age ))
}

cop_alert_active() {
  # Persistent state is authoritative across service restarts. The runtime
  # marker is a fail-safe for the brief interval around an atomic state update
  # or an unreadable state file.
  if [[ -f "$state_path" && ! -L "$state_path" ]]; then
    if /usr/bin/jq -e '.cop_alert == true' "$state_path" >/dev/null 2>&1; then
      return 0
    fi
    if /usr/bin/jq -e '.cop_alert == false' "$state_path" >/dev/null 2>&1; then
      return 1
    fi
  fi
  [[ -f "$active_marker" && ! -L "$active_marker" ]]
}

if ! cop_alert_active; then
  echo "COP ALERT inactive: ignition may turn ext_flood off"
  exit 0
fi

if fresh_regular_file "$engine_marker" "$engine_max_age"; then
  echo "fresh C-CAN engine-running evidence: ignition may turn ext_flood off"
  exit 0
fi

echo "COP ALERT active without fresh engine-running evidence: preserving ext_flood"
exit 1
