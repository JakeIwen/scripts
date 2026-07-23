#!/bin/zsh
# Quietly start Time Machine only when vanpi's exact backup disk is mounted.
set -u

host="${TM_PREFLIGHT_HOST:-VANPI.lan}"
ssh_target="${TM_PREFLIGHT_SSH_TARGET:-pi@$host}"
backup_label="${TM_PREFLIGHT_BACKUP_LABEL:-mbp2tbkup}"
remote_gate="${TM_PREFLIGHT_REMOTE_GATE:-/home/pi/scripts/samba_require_mount.sh}"

nc_command="${TM_PREFLIGHT_NC:-/usr/bin/nc}"
ssh_command="${TM_PREFLIGHT_SSH:-/usr/bin/ssh}"
tmutil_command="${TM_PREFLIGHT_TMUTIL:-/usr/bin/tmutil}"

check_only=0
if (( $# == 1 )) && [[ "$1" == "--check" ]]; then
  check_only=1
elif (( $# != 0 )); then
  print -u2 -- "usage: ${0:t} [--check]"
  exit 2
fi

skip() {
  if (( check_only )); then
    print -u2 -- "not ready: $1"
    exit 1
  fi
  exit 0
}

"$nc_command" -G 3 -z "$host" 445 >/dev/null 2>&1 ||
  skip "$host SMB is unreachable"

"$ssh_command" \
  -o BatchMode=yes \
  -o ConnectTimeout=5 \
  -o ConnectionAttempts=1 \
  -o ServerAliveInterval=5 \
  -o ServerAliveCountMax=1 \
  -o StrictHostKeyChecking=yes \
  -o LogLevel=ERROR \
  "$ssh_target" \
  /usr/bin/timeout 8 "$remote_gate" "$backup_label" \
  >/dev/null 2>&1 ||
  skip "$backup_label is not mounted safely on $host"

if (( check_only )); then
  print -- "ready: $backup_label on $host"
  exit 0
fi

# startbackup is itself a no-op when a backup is already running. --auto is
# Apple's supported mode for custom schedulers.
"$tmutil_command" startbackup --auto >/dev/null 2>&1
