#!/bin/bash
# swayidle hook for ~/.config/labwc/autostart. Neither VLC 3 nor the Van TV
# receiver's mpv (drawn through Xwayland) sends a Wayland idle-inhibit, so
# while the desktop is idle a background watcher owns the screen: it blanks
# whenever no player is open, and blanks again after a player that woke the
# screen itself (a cast, the video library) closes. "on" (swayidle's resume)
# stops the watcher and wakes the screen.
pidfile="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/blank-when-idle.pid"

player_open() { pgrep -x vlc >/dev/null || pgrep -x mpv >/dev/null; }

watch_players() {
  while true; do
    # Van TV restarts mpv between queued videos; the second look spans that gap.
    while player_open || { sleep 5; player_open; }; do sleep 30; done
    wlopm --off '*'
    until player_open; do sleep 5; done
  done
}

stop_watcher() {
  [ -f "$pidfile" ] || return 0
  local pid
  pid=$(cat "$pidfile")
  pkill -P "$pid" 2>/dev/null
  kill "$pid" 2>/dev/null
  rm -f "$pidfile"
}

case "$1" in
  off)
    # swayidle -w waits for this command, and would hold back "on" meanwhile.
    stop_watcher
    watch_players </dev/null >/dev/null 2>&1 &
    echo $! > "$pidfile"
    ;;
  on)
    stop_watcher
    wlopm --on '*'
    ;;
  *) echo "usage: $0 off|on" >&2; exit 2 ;;
esac
