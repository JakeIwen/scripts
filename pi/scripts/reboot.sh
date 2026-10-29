#!/bin/bash

export DISPLAY=:0 # set display for non-user
export WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0}
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u pi)}

if ! command -v wlopm >/dev/null 2>&1; then
  echo "cannot read monitor state, skipping scheduled reboot $(date)"
  exit 0
fi

if ! monitor_state=$(wlopm); then
  echo "cannot read monitor state, skipping scheduled reboot $(date)"
  exit 0
fi

if [[ -z "$monitor_state" ]]; then
  echo "cannot read monitor state, skipping scheduled reboot $(date)"
  exit 0
fi

printf '%s\n' "$monitor_state" | awk '
  NF != 2 || ($2 != "on" && $2 != "off") { invalid = 1 }
  $2 == "on" { any_on = 1 }
  NF == 2 && ($2 == "on" || $2 == "off") { outputs++ }
  END {
    if (invalid || outputs == 0 || outputs != NR) exit 2
    if (any_on) exit 1
    exit 0
  }
'
monitor_status=$?
case "$monitor_status" in
  0)
    echo "Monitor is off, maintenance rebooting at $(date)"
    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    "$script_dir/safe_reboot.sh"
    ;;
  1)
    echo "Monitor is on, skipping scheduled reboot $(date)"
    ;;
  *)
    echo "cannot read monitor state, skipping scheduled reboot $(date)"
    ;;
esac
