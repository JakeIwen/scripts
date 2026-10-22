#!/bin/bash
# Legacy direct-VLC starts use this; manager/Van TV starts own their timers.
# Hold one per-user job across independent ssh shells without blocking playback.
exec 9>"${XDG_RUNTIME_DIR:-/run/user/$UID}/rear-movie-check.lock" || exit 1
flock -n 9 || exit 0
sleep 1.5
if ! timeout --kill-after=5 45 bash "$HOME/sns.sh" ensure_rear_movie; then
  logger -t rear-movie "Sonos check failed after VLC start"
  exit 1
fi
