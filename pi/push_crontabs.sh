#!/bin/bash
# install the repo crontab files onto the pi (shows a diff of what changes)
#   crontab    -> root's crontab
#   crontab_pi -> pi user's crontab
set -eu
dir=$(cd "$(dirname "$0")" && pwd)  # subshell only; caller's pwd untouched
pi_ip="${vanpi:-pi@vanpi.lan}"

install_crontab() { # <local file> <sudo|"">
  local file=$dir/$1 as=${2:-}
  [ -s "$file" ] || { echo "refusing: $file missing or empty"; exit 1; }
  echo "== $file -> ${as:-pi} crontab on $pi_ip (< current, > incoming):"
  ssh "$pi_ip" "$as crontab -l" 2>/dev/null | diff - "$file" && echo "(no changes)" || true
  ssh "$pi_ip" "$as crontab -" < "$file"
  echo "installed $file"
}

install_crontab crontab sudo
install_crontab crontab_pi ""
