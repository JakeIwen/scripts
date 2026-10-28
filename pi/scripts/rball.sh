#!/bin/bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
safe_reboot="$script_dir/safe_reboot.sh"
reboot_delay=5
reboot_log="$HOME/.rball-reboot.log"
ssh_options=(-n -o BatchMode=yes -o ConnectTimeout=5
  -o ServerAliveInterval=5 -o ServerAliveCountMax=3
  -o StrictHostKeyChecking=yes -o UpdateHostKeys=no)
antenna=ubnt@192.168.8.20
router=root@192.168.6.1

dry_run=no
case "$#:${1:-}" in
  0:) ;;
  1:--dry-run) dry_run=yes ;;
  1:--help|1:-h) echo "usage: ${0##*/} [--dry-run]"; exit 0 ;;
  *) echo "usage: ${0##*/} [--dry-run]" >&2; exit 2 ;;
esac

[[ -x "$safe_reboot" ]] || {
  echo "ERROR: safe reboot helper is not executable: $safe_reboot" >&2
  exit 1
}
for required in ssh nohup sleep uptime; do
  command -v "$required" >/dev/null || exit 1
done
if [[ "$dry_run" == no ]]; then
  umask 077
  exec 3>>"$reboot_log"
fi

remote_reboot() {
  local name=$1 target=$2 action=reboot
  shift 2
  if [[ "$dry_run" == yes ]]; then
    echo "Would reboot $name ($target); checking uptime."
    action=uptime
  else
    echo "Rebooting $name ($target)."
  fi
  ssh "${ssh_options[@]}" "$@" "$target" "$action" || {
    local status=$?
    echo "ERROR: $name command failed; stopping before the remaining reboots." >&2
    return "$status"
  }
}

# The antenna is reached through the router; the Pi must outlive both SSH calls.
# Match ubnt_wifi.py's explicit RSA identity; the manager has no reboot command.
remote_reboot antenna "$antenna" -o IdentitiesOnly=yes -i "$HOME/.ssh/id_rsa"
remote_reboot router "$router"
if [[ "$dry_run" == yes ]]; then
  echo "Would detach $safe_reboot after ${reboot_delay}s; checking Pi uptime."
  uptime
  exit 0
fi

# Close the caller's streams and ignore hangup so an SSH Shortcut can return.
nohup /bin/bash -c 'sleep "$1" && exec "$2"' rball "$reboot_delay" "$safe_reboot" \
  </dev/null >&3 2>&1 3>&- &
echo "Pi safe reboot queued after ${reboot_delay}s; log: $reboot_log"
