#! /bin/bash

ISW_LOCK_FILE=${ISW_LOCK_FILE:-/home/pi/.internet_switches.lock}
ISW_LOCK_WAIT_SECONDS=${ISW_LOCK_WAIT_SECONDS:-55}
ISW_CANARY_DIR=${ISW_CANARY_DIR:-/run/lock/vanpi-internet-switches-canary}
ISW_OWNER_FILE="$ISW_CANARY_DIR/owner"
ISW_ALERT_FILE="$ISW_CANARY_DIR/alerted"
ISW_ALERT_LOCK="$ISW_CANARY_DIR/alert.lock"
ISW_LOG_FILE=${ISW_LOG_FILE:-/var/log/cron/internet_switches.log}
ISW_NTFY_SEND=${ISW_NTFY_SEND:-/home/pi/scripts/ntfy_send.sh}
ISW_MWAN_HOST=${ISW_MWAN_HOST:-root@OpenWrt}
ISW_MWAN_TIMEOUT_SECONDS=${ISW_MWAN_TIMEOUT_SECONDS:-12}
ISW_MWAN_STATE=""

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

conf() { cat /home/pi/mconf/$1* &> /dev/null; }

ubnt_internet_ops() { # nanostation connected; van is likely stationary/parked
  echo 'ubnt_internet_ops'
  mount_drives
  sleep 1
  if conf notorrent || has_io_error '/mnt/movingparts' || starlink_notor
  then kill_torrent_client 
  else start_torrent_client
  fi
}

starlink_notor() {
  local status=$(/home/pi/scripts/tuya_status.sh starlink)
  if [ "$status" = "on" ]; then
    ls /home/pi/starconf/notor &> /dev/null
  else
    ls /home/pi/starconf/idkhowtoreturnfalse &> /dev/null
  fi
}

has_io_error() { ls -lah "$1" 2>&1 | grep -q 'Input/output error'; }
# if has_io_error '/mnt/movingparts'; then echo 'i/o error'; fi

update_iface_score() {
  file=$1
  iface_score=`cat $file` 
  if [ "$iface_score" -lt "$lowest_score" ]; then
    echo "IS LES"
    lowest_score=$iface_score
    iface=`basename "$(dirname $file)"`
    echo "set iface. l: $lowest_score, f: $file, iface: $iface"
  fi
}

mobile_internet_ops() {
  echo 'mobile_internet_ops'
  if conf mtorrent; then ubnt_internet_ops; else no_internet_ops; fi
}

lifi_internet_ops() {
  echo 'lifi_internet_ops'
  if conf mtorrent_lifi; then
    echo "lifi-tor allowed"
    ubnt_internet_ops
  else
    no_internet_ops
  fi
}

no_internet_ops() {
  echo 'no_internet_ops'
  if conf mtorrent; then 
    mount_drives
    start_torrent_client
  elif conf mdisk; then 
    mount_drives
    kill_torrent_client
  else 
    kill_torrent_client
    unmount_drives
  fi 
    
  
}

kill_torrent_client() {
  if [[ "$(ps ax)" == *"qbittorrent"* ]]; then echo 'killtorrent' && pkill -TERM qbittorrent; fi
  sleep 2
  if [[ "$(ps ax)" == *"qbittorrent"* ]]; then echo 'SECOND ATTEMPT killtorrent' && pkill -f qbittorrent; fi
  sleep 2
}

start_torrent_client() {
  if [[ "$(grep movingparts /proc/mounts)" ]]; then 
    if ! pgrep qbittor >/dev/null; then
      # Background only qbittorrent—not the surrounding conditional—and do
      # not let the long-lived client inherit fd 9 and hold our flock.
      nohup qbittorrent-nox 9>&- &
    fi
  else
    echo "preventing torrent-without-mpdisk"
    kill_torrent_client
  fi
}

mount_drives() {
  if [[ $(van_is_running) ]]; then
    echo "MOUNT interrupt: van is running, unmounting drives"
    echo "will not mount drives without idisk conf flag!"
    kill_torrent_client
    stop_service smbd 
    sleep 1
    unmount_drives
  else
    /home/pi/scripts/umount_disks.sh --clear-spindown-state || return 1
    . /home/pi/scripts/mount_disks.sh
    sleep 3
    echo "drives mounted. starting smb share."
    start_service smbd 
  fi
}

van_is_running() {
  if test -f /home/pi/hooks/ignition_is_on; then
    [ -z "$(conf idisk)" ] && echo "yes"
  fi
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
  kill_torrent_client
  sleep 4
  unmount_drives
}

refresh_mwan_state() {
  local state
  local status

  state="$(/usr/bin/timeout --kill-after=2s "${ISW_MWAN_TIMEOUT_SECONDS}s" \
    /usr/bin/ssh \
      -n \
      -o BatchMode=yes \
      -o ConnectTimeout=5 \
      -o ServerAliveInterval=3 \
      -o ServerAliveCountMax=1 \
      "$ISW_MWAN_HOST" \
      '/usr/sbin/mwan3 interfaces' 2>&1)"
  status=$?
  if (( status != 0 )); then
    echo "ERROR: unable to query mwan3 state from $ISW_MWAN_HOST (status $status)" >&2
    printf '%s\n' "$state" >&2
    return 1
  fi
  if [[ -z "$state" ]] || ! /usr/bin/grep -Fq 'Interface status:' <<< "$state"; then
    echo "ERROR: mwan3 returned an empty or unrecognized interface report" >&2
    return 1
  fi

  ISW_MWAN_STATE="$state"
}

iface_online() {
  local interface="$1"
  /usr/bin/grep -Fq " interface $interface is online" <<< "$ISW_MWAN_STATE"
}

# if date | grep '0:0'; then date; fi
# ubnt_internet_ops
set_isw_options() {
  echo ""
  echo "$(date)"
  if conf nodisk &> /dev/null; then kill_all # drives disabled ~/mconf/nodisk
  else
    # WAN transitions happen behind the Pi's stable LAN connection. OpenWrt
    # pushes a reconciliation request, then the Pi verifies current mwan3
    # state once here. A failed query is not evidence that every WAN is down.
    refresh_mwan_state || return 1
    if iface_online clientwan; then mobile_internet_ops
    elif iface_online lifiwan; then lifi_internet_ops
    elif iface_online wan; then ubnt_internet_ops
    else no_internet_ops
    fi
  fi
}
#
internet_switches_main() {
  local rc

  # The ignition hook can invoke this at the same time as the minutely cron
  # job. Serialize the entire policy decision so mounting and unmounting cannot
  # race. A waiter that times out acts as the watchdog for a stuck lock holder.
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
