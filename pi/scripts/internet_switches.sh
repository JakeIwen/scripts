#! /bin/bash

ISW_LOCK_FILE=${ISW_LOCK_FILE:-/home/pi/.internet_switches.lock}
ISW_LOCK_WAIT_SECONDS=${ISW_LOCK_WAIT_SECONDS:-55}
ISW_CANARY_DIR=${ISW_CANARY_DIR:-/run/lock/vanpi-internet-switches-canary}
ISW_OWNER_FILE="$ISW_CANARY_DIR/owner"
ISW_ALERT_FILE="$ISW_CANARY_DIR/alerted"
ISW_ALERT_LOCK="$ISW_CANARY_DIR/alert.lock"
ISW_LOG_FILE=${ISW_LOG_FILE:-/var/log/cron/internet_switches.log}
ISW_NTFY_SEND=${ISW_NTFY_SEND:-/home/pi/scripts/ntfy_send.sh}
ISW_POLICYCTL=${ISW_POLICYCTL:-/home/pi/scripts/policyctl}
ISW_IGNITION_FLAG=${ISW_IGNITION_FLAG:-/home/pi/hooks/ignition_is_on}
ISW_TUYA_STATUS=${ISW_TUYA_STATUS:-/home/pi/scripts/tuya_status.sh}
ISW_PGREP=${ISW_PGREP:-/usr/bin/pgrep}
ISW_PKILL=${ISW_PKILL:-/usr/bin/pkill}
ISW_SLEEP=${ISW_SLEEP:-/usr/bin/sleep}
ISW_QBIT_PROCESS=${ISW_QBIT_PROCESS:-qbittorrent-nox}
ISW_QBIT_BINARY=${ISW_QBIT_BINARY:-/usr/bin/qbittorrent-nox}
ISW_MOUNT_DISKS=${ISW_MOUNT_DISKS:-/home/pi/scripts/mount_disks.sh}
ISW_ABORT_BACKUP=${ISW_ABORT_BACKUP:-/home/pi/scripts/backup/abort_backup.sh}
POLICY_DISKS_ENABLED=""
POLICY_TORRENTS_ENABLED=""
POLICY_ALLOW_STARLINK_TORRENTS=""

isw_prepare_canary_dir() {
  if [[ -L "$ISW_CANARY_DIR" || ( -e "$ISW_CANARY_DIR" && ! -d "$ISW_CANARY_DIR" ) ]]; then
    echo "ERROR: $ISW_CANARY_DIR is not a safe canary state directory" >&2
    return 1
  fi
  /usr/bin/install -d -m 700 -- "$ISW_CANARY_DIR"
}

isw_record_owner() {
  local started tmp
  isw_prepare_canary_dir || return 1
  started=$(/usr/bin/date +%s) || return 1
  tmp="$ISW_CANARY_DIR/.owner.$$"
  if ! (umask 077; printf '%s %s\n' "$$" "$started" > "$tmp"); then
    echo "ERROR: cannot write internet_switches owner state" >&2
    return 1
  fi
  if ! /usr/bin/mv -f -- "$tmp" "$ISW_OWNER_FILE"; then
    /usr/bin/rm -f -- "$tmp"
    echo "ERROR: cannot install internet_switches owner state" >&2
    return 1
  fi
}

isw_cleanup_owner() {
  local recorded_pid recorded_started
  [[ -f "$ISW_OWNER_FILE" ]] || return 0
  IFS=' ' read -r recorded_pid recorded_started < "$ISW_OWNER_FILE" || return 0
  if [[ "$recorded_pid" == "$$" ]]; then
    /usr/bin/rm -f -- "$ISW_OWNER_FILE" || true
  fi
}

isw_process_snapshot() {
  local owner_pid=$1 snapshot

  if [[ "$owner_pid" =~ ^[0-9]+$ ]] && kill -0 "$owner_pid" 2>/dev/null; then
    snapshot=$(/usr/bin/ps -eo pid=,ppid=,stat=,etimes=,wchan:24=,comm= 2>&1 |
      /usr/bin/awk -v root="$owner_pid" '
        $1 == root || selected[$2] { selected[$1] = 1; print }
      ')
  fi

  if [[ -z "$snapshot" ]]; then
    snapshot=$(/usr/bin/ps -eo pid=,ppid=,stat=,etimes=,wchan:24=,comm= 2>&1 |
      /usr/bin/awk '
        $6 ~ /^(internet_switc|bash|ssh|curl|ls|mount|umount|blkid|findmnt|fsck\.hfsplus|hd-idle|sudo|service|systemctl)$/ { print }
      ')
  fi

  printf '%s\n' "${snapshot:-no relevant process snapshot available}"
}

isw_lock_diagnostics() {
  local owner_pid=unknown owner_started=unknown now elapsed=unknown
  local elapsed_display lock_snapshot process_snapshot log_tail diagnostics

  if [[ -f "$ISW_OWNER_FILE" ]]; then
    IFS=' ' read -r owner_pid owner_started < "$ISW_OWNER_FILE" || {
      owner_pid=unknown
      owner_started=unknown
    }
  fi
  now=$(/usr/bin/date +%s 2>/dev/null || true)
  if [[ "$now" =~ ^[0-9]+$ && "$owner_started" =~ ^[0-9]+$ ]]; then
    elapsed=$((now - owner_started))
  fi
  if [[ "$elapsed" =~ ^[0-9]+$ ]]; then
    elapsed_display="${elapsed}s"
  else
    elapsed_display=unknown
  fi

  lock_snapshot=$(/usr/bin/timeout 3 /usr/bin/lslocks -n \
    -o PID,COMMAND,TYPE,MODE,PATH 2>&1 | /usr/bin/grep -F "$ISW_LOCK_FILE" || true)
  process_snapshot=$(isw_process_snapshot "$owner_pid")
  log_tail=$(/usr/bin/timeout 3 /usr/bin/tail -n 16 -- "$ISW_LOG_FILE" 2>&1 || true)

  diagnostics=$(printf '%s\n' \
    "internet_switches could not acquire its lifecycle lock after ${ISW_LOCK_WAIT_SECONDS}s." \
    "Recorded holder PID: $owner_pid; lock age: $elapsed_display" \
    "" \
    "Lock snapshot (PID COMMAND TYPE MODE PATH):" \
    "${lock_snapshot:-no lock snapshot available}" \
    "" \
    "Holder process tree (PID PPID STAT ELAPSED WCHAN COMMAND; arguments omitted):" \
    "$process_snapshot" \
    "" \
    "Recent internet_switches log:" \
    "${log_tail:-no recent log output available}" |
    /usr/bin/head -c 3500)
  printf '%s' "$diagnostics"
}

isw_write_alert_marker() {
  local tmp
  isw_prepare_canary_dir || return 1
  tmp="$ISW_CANARY_DIR/.alerted.$$"
  if ! (umask 077; /usr/bin/date -Is > "$tmp"); then
    echo "ERROR: cannot write internet_switches alert state" >&2
    return 1
  fi
  if ! /usr/bin/mv -f -- "$tmp" "$ISW_ALERT_FILE"; then
    /usr/bin/rm -f -- "$tmp"
    echo "ERROR: cannot install internet_switches alert state" >&2
    return 1
  fi
}

isw_notify_lock_timeout() {
  local diagnostics rc=0
  isw_prepare_canary_dir || return 1

  exec 8>"$ISW_ALERT_LOCK" || return 1
  if ! /usr/bin/flock -n 8; then
    echo "another lock-timeout waiter is handling the internet_switches alert"
    exec 8>&-
    return 0
  fi

  if [[ -f "$ISW_ALERT_FILE" ]]; then
    echo "internet_switches lock-timeout alert already sent; suppressing repeat"
  else
    diagnostics=$(isw_lock_diagnostics)
    if ! "$ISW_NTFY_SEND" \
        "vanpi internet_switches stalled" "$diagnostics" high warning; then
      rc=1
    elif ! isw_write_alert_marker; then
      rc=1
    fi
  fi

  /usr/bin/flock -u 8 || true
  exec 8>&-
  return "$rc"
}

isw_notify_recovery() {
  [[ -f "$ISW_ALERT_FILE" ]] || return 0
  "$ISW_NTFY_SEND" \
    "vanpi internet_switches recovered" \
    "A later internet_switches policy run acquired the lifecycle lock and completed successfully." \
    default white_check_mark || return 1
  /usr/bin/rm -f -- "$ISW_ALERT_FILE"
}

ignition_is_on() {
  test -f "$ISW_IGNITION_FLAG"
}

load_requested_policy() {
  local output status
  output=$("$ISW_POLICYCTL" read 2>&1)
  status=$?
  if (( status != 0 )); then
    echo "ERROR: unable to read requested policy (status $status): $output" >&2
    return 1
  fi
  read -r POLICY_DISKS_ENABLED POLICY_TORRENTS_ENABLED \
    POLICY_ALLOW_STARLINK_TORRENTS <<< "$output"
  if [[ ! "$POLICY_DISKS_ENABLED" =~ ^[01]$ ||
        ! "$POLICY_TORRENTS_ENABLED" =~ ^[01]$ ||
        ! "$POLICY_ALLOW_STARLINK_TORRENTS" =~ ^[01]$ ]]; then
    echo "ERROR: policyctl returned an invalid compact policy: $output" >&2
    return 1
  fi
}

starlink_blocks_torrents() {
  local state status

  # Explicit permission makes the Starlink state irrelevant. The global
  # torrent switch is checked separately and still takes precedence.
  [[ "$POLICY_ALLOW_STARLINK_TORRENTS" == 1 ]] && return 1

  state=$("$ISW_TUYA_STATUS" starlink 2>&1)
  status=$?
  if (( status != 0 )); then
    echo "WARNING: Starlink state is unavailable; blocking torrents: $state" >&2
    return 0
  fi
  case "$state" in
    off) return 1 ;;
    on)
      echo "Starlink is on and Starlink torrents are not allowed"
      return 0
      ;;
    *)
      echo "WARNING: unrecognized Starlink state '$state'; blocking torrents" >&2
      return 0
      ;;
  esac
}

has_io_error() { ls -lah "$1" 2>&1 | grep -q 'Input/output error'; }
# if has_io_error '/mnt/movingparts'; then echo 'i/o error'; fi

kill_torrent_client() {
  local rc
  local attempt

  "$ISW_PGREP" -x "$ISW_QBIT_PROCESS" >/dev/null 2>&1
  rc=$?
  (( rc == 1 )) && return 0
  if (( rc != 0 )); then
    echo "ERROR: cannot inspect $ISW_QBIT_PROCESS processes (pgrep status $rc)" >&2
    return 1
  fi

  echo "asking $ISW_QBIT_PROCESS to stop"
  "$ISW_PKILL" -TERM -x "$ISW_QBIT_PROCESS"
  rc=$?
  (( rc <= 1 )) || return 1

  # qBittorrent can need about 18 seconds to save state and exit. Keep the
  # shutdown graceful, but do not let policy reconciliation wait forever.
  for attempt in {1..30}; do
    "$ISW_PGREP" -x "$ISW_QBIT_PROCESS" >/dev/null 2>&1
    rc=$?
    (( rc == 1 )) && return 0
    if (( rc != 0 )); then
      echo "ERROR: cannot verify $ISW_QBIT_PROCESS shutdown (pgrep status $rc)" >&2
      return 1
    fi
    "$ISW_SLEEP" 1
  done

  echo "ERROR: $ISW_QBIT_PROCESS did not stop within 30 seconds" >&2
  return 1
}

start_torrent_client() {
  local rc

  if [[ "$(grep movingparts /proc/mounts)" ]]; then 
    "$ISW_PGREP" -x "$ISW_QBIT_PROCESS" >/dev/null 2>&1
    rc=$?
    if (( rc == 1 )); then
      # Background only qbittorrent—not the surrounding conditional—and do
      # not let the long-lived client inherit fd 9 and hold our flock.
      /usr/bin/nohup "$ISW_QBIT_BINARY" 9>&- &
    elif (( rc != 0 )); then
      echo "ERROR: cannot inspect $ISW_QBIT_PROCESS processes (pgrep status $rc)" >&2
      return 1
    fi
  else
    echo "preventing torrent-without-mpdisk"
    kill_torrent_client
  fi
}

recover_stale_mounts_if_needed() {
  local stale_mounts
  local status

  stale_mounts=$("$ISW_MOUNT_DISKS" --list-stale)
  status=$?
  if (( status != 0 )); then
    echo "ERROR: cannot inspect managed targets for stale mounts (status $status)" >&2
    return 1
  fi
  [[ -n "$stale_mounts" ]] || return 0

  echo "stale managed mounts detected:"
  printf '%s\n' "$stale_mounts"

  # A disconnected filesystem may still be held by a backup, qBittorrent, or
  # Samba. Stop each consumer before attempting only a normal unmount. The
  # recovery helper never uses force or lazy detach and revalidates the source
  # immediately before changing it.
  "$ISW_ABORT_BACKUP" || {
    echo "ERROR: backup/restore did not stop; refusing stale-mount recovery" >&2
    return 1
  }
  kill_torrent_client || {
    echo "ERROR: qBittorrent did not stop; refusing stale-mount recovery" >&2
    return 1
  }
  stop_service smbd || {
    echo "ERROR: smbd did not stop; refusing stale-mount recovery" >&2
    return 1
  }
  "$ISW_MOUNT_DISKS" --recover-stale
}

mount_drives() {
  if [[ $(van_is_running) ]]; then
    echo "MOUNT interrupt: van is running, unmounting drives"
    echo "will not mount drives while ignition is on"
    kill_torrent_client
    stop_service smbd 
    sleep 1
    unmount_drives
    return 1
  else
    /home/pi/scripts/umount_disks.sh --clear-spindown-state || return 1
    "$ISW_MOUNT_DISKS" || return 1
    sleep 3
    echo "drives mounted. starting smb share."
    start_service smbd 
  fi
}

mount_always_available_drives() {
  "$ISW_MOUNT_DISKS" --always
}

van_is_running() {
  ignition_is_on && echo "yes"
}

unmount_drives() {
  /home/pi/scripts/umount_disks.sh --spindown
}

stop_service() {
  sudo /usr/sbin/service $1 stop
}

start_service() {
  /usr/sbin/service $1 status > /dev/null || sudo /usr/sbin/service $1 start
}

kill_all() {
  echo 'killing all'
  kill_torrent_client || return 1
  sleep 4
  unmount_drives
}

set_isw_options() {
  local shutdown_status always_mount_status torrent_status

  echo ""
  echo "$(date)"
  # Stale mounts are observed unsafe runtime state, not requested policy. Deal
  # with them before any policy branch so a vanished device never remains
  # exposed merely because ignition or the requested configuration changed.
  recover_stale_mounts_if_needed || return 1
  # Ignition is observed safety state and always wins, even if requested
  # policy is missing or corrupt.
  if ignition_is_on; then
    echo "ignition is on; disabling and spinning down HDDs"
    kill_all
    shutdown_status=$?
    # Flash media remains available while driving. Run this even if HDD
    # shutdown reports a failure, but preserve both outcomes.
    mount_always_available_drives
    always_mount_status=$?
    (( shutdown_status == 0 && always_mount_status == 0 ))
    return
  fi

  # Always-available flash media is independent from requested HDD policy. A
  # flash-mount failure must not prevent the HDD/torrent policy from reaching
  # its own safe state, but the combined run still reports the failure.
  mount_always_available_drives
  always_mount_status=$?

  load_requested_policy || return 1
  if [[ "$POLICY_DISKS_ENABLED" == 0 ]]; then
    echo "requested policy disables HDDs"
    kill_all
    shutdown_status=$?
    (( always_mount_status == 0 && shutdown_status == 0 ))
    return
  fi

  # Parked storage is normally available. Uplink attachment no longer decides
  # whether it is safe to spin disks; ignition_monitor owns that decision.
  mount_drives || return 1

  if [[ "$POLICY_TORRENTS_ENABLED" == 0 ]]; then
    echo "requested policy disables torrents"
    kill_torrent_client
  elif has_io_error '/mnt/movingparts'; then
    echo "movingparts has an I/O error; disabling torrents" >&2
    kill_torrent_client
  elif starlink_blocks_torrents; then
    kill_torrent_client
  else
    start_torrent_client
  fi
  torrent_status=$?
  (( always_mount_status == 0 && torrent_status == 0 ))
}
#
internet_switches_main() {
  local rc

  # Ignition hooks, event-driven services, and the periodic timer can overlap.
  # Serialize the entire policy decision so mounting and unmounting cannot race.
  # policy_watchdog.sh independently alerts if the lock holder stalls.
  exec 9>"$ISW_LOCK_FILE" || return 1
  if ! /usr/bin/flock -w "$ISW_LOCK_WAIT_SECONDS" 9; then
    echo "another internet_switches.sh instance held the lock for $ISW_LOCK_WAIT_SECONDS seconds"
    isw_notify_lock_timeout || true
    exec 9>&-
    return 1
  fi

  isw_record_owner || true
  trap isw_cleanup_owner EXIT

  set_isw_options
  rc=$?
  if (( rc == 0 )); then
    isw_notify_recovery || true
  fi

  isw_cleanup_owner
  trap - EXIT
  /usr/bin/flock -u 9 || true
  exec 9>&-
  return "$rc"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  internet_switches_main "$@"
  internet_switches_rc=$?
  exit "$internet_switches_rc"
fi
