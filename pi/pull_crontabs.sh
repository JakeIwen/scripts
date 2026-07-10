#!/bin/bash
# snapshot the pi's live crontabs into the repo files
#   root's crontab -> crontab, pi user's -> crontab_pi
set -eu
dir=$(cd "$(dirname "$0")" && pwd)  # subshell only; caller's pwd untouched
pi_ip="${vanpi:-pi@vanpi.lan}"

pull_crontab() { # <sudo|""> <dest file>
  local as=$1 dest=$dir/$2 tmp
  tmp=$(mktemp)
  ssh "$pi_ip" "$as crontab -l" > "$tmp"
  # a wiped remote crontab must never truncate a good repo copy
  [ -s "$tmp" ] || { echo "refusing: remote ${as:-pi} crontab is empty, keeping $dest"; rm -f "$tmp"; exit 1; }
  mv "$tmp" "$dest"
  echo "pulled $dest"
}

pull_crontab sudo crontab
pull_crontab "" crontab_pi
