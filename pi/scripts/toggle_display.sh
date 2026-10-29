#! /bin/bash

export WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-wayland-0}
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u pi)}

fallback_xset() {
  if [[ -n "$1" ]]; then
    xset dpms force "$1"
  elif [[ -z $(xset q | grep 'Monitor is On') ]]; then
    xset dpms force on
  else
    xset dpms force off
  fi
}

if (( $# > 1 )); then
  echo "usage: $0 [on|off]" >&2
  exit 2
fi

new_status=$1 # on, off
if [[ -n "$new_status" && "$new_status" != on && "$new_status" != off ]]; then
  echo "usage: $0 [on|off]" >&2
  exit 2
fi

if ! command -v wlopm >/dev/null 2>&1; then
  fallback_xset "$new_status"
  exit $?
fi

if [[ -n "$new_status" ]]; then
  wlopm "--$new_status" '*' && exit 0
  fallback_xset "$new_status"
  exit $?
fi

if output=$(wlopm); then
  if printf '%s\n' "$output" | grep -Eq '(^|[[:space:]])on$'; then
    new_status=off
  else
    new_status=on
  fi
  wlopm "--$new_status" '*' && exit 0
fi

fallback_xset ""
