#!/bin/bash
# Daily directly browsable EXFAT snapshots on a dedicated ext4 HDD.
#
# Each completed directory is a full point-in-time tree. rsync --link-dest
# hard-links unchanged files to the previous completed snapshot, so unchanged
# data occupies blocks only once. A hidden partial directory is never presented
# as a valid snapshot and can be resumed after an interruption.
set -u
umask 027

backup_conf=${EXFAT_SNAPSHOT_CONF:-/home/pi/scripts/backup/backup_conf.sh}
policyctl=${EXFAT_SNAPSHOT_POLICYCTL:-/home/pi/scripts/policyctl}
diskctl=${EXFAT_SNAPSHOT_DISKCTL:-/home/pi/scripts/diskctl}
umount_disks=${EXFAT_SNAPSHOT_UMOUNT_DISKS:-/home/pi/scripts/umount_disks.sh}
notify_command=${EXFAT_SNAPSHOT_NOTIFY:-/home/pi/scripts/ntfy_send.sh}
findmnt_command=${EXFAT_SNAPSHOT_FINDMNT:-/usr/bin/findmnt}
readlink_command=${EXFAT_SNAPSHOT_READLINK:-/usr/bin/readlink}
rsync_command=${EXFAT_SNAPSHOT_RSYNC:-/usr/bin/rsync}
find_command=${EXFAT_SNAPSHOT_FIND:-/usr/bin/find}
date_command=${EXFAT_SNAPSHOT_DATE:-/usr/bin/date}
sync_command=${EXFAT_SNAPSHOT_SYNC:-/usr/bin/sync}
flock_command=${EXFAT_SNAPSHOT_FLOCK:-/usr/bin/flock}
timeout_command=${EXFAT_SNAPSHOT_TIMEOUT:-/usr/bin/timeout}
touch_command=${EXFAT_SNAPSHOT_TOUCH:-/usr/bin/touch}
rm_command=${EXFAT_SNAPSHOT_RM:-/usr/bin/rm}
mv_command=${EXFAT_SNAPSHOT_MV:-/usr/bin/mv}
install_command=${EXFAT_SNAPSHOT_INSTALL:-/usr/bin/install}
df_command=${EXFAT_SNAPSHOT_DF:-/usr/bin/df}
smartctl_command=${EXFAT_SNAPSHOT_SMARTCTL:-/usr/sbin/smartctl}
ps_command=${EXFAT_SNAPSHOT_PS:-/usr/bin/ps}
sleep_command=${EXFAT_SNAPSHOT_SLEEP:-/usr/bin/sleep}
block_stat_root=${EXFAT_SNAPSHOT_BLOCK_STAT_ROOT:-/sys/class/block}
lifecycle_lock=${EXFAT_SNAPSHOT_LIFECYCLE_LOCK:-/home/pi/.internet_switches.lock}

# shellcheck source=backup_conf.sh
. "$backup_conf" || exit 1
telemetry_seconds=${EXFAT_SNAPSHOT_TELEMETRY_SECONDS:-300}

notify() { "$notify_command" "$@"; }
log() { echo "[$("$date_command" '+%F %T')] $*"; }
fail() {
  log "FATAL: $1"
  notify "vanpi EXFAT backup FAILED" "$1" high rotating_light
  exit 1
}

force=0
case "$#" in
  0) ;;
  1)
    if [[ "$1" == --force ]]; then
      force=1
    else
      echo "usage: ${0##*/} [--force]" >&2
      exit 2
    fi
    ;;
  *)
    echo "usage: ${0##*/} [--force]" >&2
    exit 2
    ;;
esac

label_re='^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
integer_re='^[1-9][0-9]{0,3}$'
for label in "$EXFAT_SNAPSHOT_SOURCE_LABEL" "$EXFAT_SNAPSHOT_DISK_LABEL"; do
  [[ "$label" =~ $label_re ]] || fail "invalid configured filesystem label"
done
[[ "$EXFAT_SNAPSHOT_PREFIX" =~ ^[A-Za-z0-9._-]+_$ ]] ||
  fail "invalid configured snapshot prefix"
[[ "$EXFAT_SNAPSHOT_ROOT" == "$EXFAT_SNAPSHOT_MNT/backups" ]] ||
  fail "snapshot root must be the backups directory directly below the exact target mount"
for value in "$EXFAT_SNAPSHOT_STALE_HOURS" "$EXFAT_SNAPSHOT_MIN_FREE_GB" \
  "$EXFAT_SNAPSHOT_KEEP_DAILY_DAYS" "$EXFAT_SNAPSHOT_KEEP_WEEKLY_DAYS" \
  "$EXFAT_SNAPSHOT_KEEP_MONTHLY_DAYS" "$telemetry_seconds"; do
  [[ "$value" =~ $integer_re ]] || fail "invalid numeric snapshot configuration"
done
(( EXFAT_SNAPSHOT_KEEP_DAILY_DAYS < EXFAT_SNAPSHOT_KEEP_WEEKLY_DAYS &&
   EXFAT_SNAPSHOT_KEEP_WEEKLY_DAYS < EXFAT_SNAPSHOT_KEEP_MONTHLY_DAYS )) ||
  fail "snapshot retention boundaries are not increasing"

for required in "$policyctl" "$diskctl" "$umount_disks" "$notify_command" \
  "$findmnt_command" "$readlink_command" "$rsync_command" "$find_command" \
  "$date_command" "$sync_command" "$flock_command" "$timeout_command" \
  "$touch_command" "$rm_command" "$mv_command" "$install_command" "$df_command"; do
  [[ -x "$required" ]] || fail "required command is not executable: $required"
done
for required in "$ps_command" "$sleep_command"; do
  [[ -x "$required" ]] || fail "required telemetry command is not executable: $required"
done

snapshot_suffix_re='[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}'
partial_name=".${EXFAT_SNAPSHOT_PREFIX%_}.partial"
complete_marker=.vanpi_snapshot_complete
child=
telemetry_child=
backup_disk_active=0
job_locked=0
skip_exit_stop=0

run() {
  "$@" &
  child=$!
  wait "$child"
  local rc=$?
  child=
  return "$rc"
}

stop_snapshot_telemetry() {
  [[ -n "$telemetry_child" ]] || return 0
  kill -TERM "$telemetry_child" 2>/dev/null || true
  wait "$telemetry_child" 2>/dev/null || true
  telemetry_child=
}

snapshot_block_stat() {
  local parent_name=$1 stat_path
  stat_path="$block_stat_root/$parent_name/stat"
  [[ -r "$stat_path" ]] || return 1
  IFS= read -r REPLY < "$stat_path"
  [[ -n "$REPLY" ]]
}

snapshot_telemetry_loop() {
  local rsync_pid=$1 parent_name=$2 sleeper= current now previous_epoch
  local reads read_merges read_sectors read_ms writes write_merges write_sectors
  local write_ms in_flight io_ms weighted_ms rest
  local prev_reads prev_read_sectors prev_read_ms prev_writes prev_write_sectors
  local prev_write_ms prev_io_ms elapsed delta_reads delta_writes
  local process_lines process_io

  trap '[[ -z "$sleeper" ]] || kill "$sleeper" 2>/dev/null; exit 0' TERM INT
  snapshot_block_stat "$parent_name" || {
    log "telemetry unavailable: cannot read block statistics for $parent_name"
    return 0
  }
  read -r reads read_merges read_sectors read_ms writes write_merges \
    write_sectors write_ms in_flight io_ms weighted_ms rest <<< "$REPLY"
  prev_reads=$reads
  prev_read_sectors=$read_sectors
  prev_read_ms=$read_ms
  prev_writes=$writes
  prev_write_sectors=$write_sectors
  prev_write_ms=$write_ms
  prev_io_ms=$io_ms
  previous_epoch=$($date_command +%s)
  log "telemetry started: rsync_pid=$rsync_pid target=$parent_name block_stat=$REPLY"

  while kill -0 "$rsync_pid" 2>/dev/null; do
    "$sleep_command" "$telemetry_seconds" &
    sleeper=$!
    wait "$sleeper" 2>/dev/null || return 0
    sleeper=
    kill -0 "$rsync_pid" 2>/dev/null || return 0
    snapshot_block_stat "$parent_name" || {
      log "telemetry warning: block statistics disappeared for $parent_name"
      return 0
    }
    current=$REPLY
    read -r reads read_merges read_sectors read_ms writes write_merges \
      write_sectors write_ms in_flight io_ms weighted_ms rest <<< "$current"
    now=$($date_command +%s)
    elapsed=$((now - previous_epoch))
    delta_reads=$((reads - prev_reads))
    delta_writes=$((writes - prev_writes))
    log "telemetry target=$parent_name interval=${elapsed}s read_ops=$delta_reads read_mib=$(((read_sectors - prev_read_sectors) / 2048)) read_avg_ms=$((delta_reads ? (read_ms - prev_read_ms) / delta_reads : 0)) write_ops=$delta_writes write_mib=$(((write_sectors - prev_write_sectors) / 2048)) write_avg_ms=$((delta_writes ? (write_ms - prev_write_ms) / delta_writes : 0)) io_busy_ms=$((io_ms - prev_io_ms)) in_flight=$in_flight"
    process_lines=$(
      "$ps_command" -o pid=,ppid=,stat=,wchan:24=,etime=,time=,comm= \
        -p "$rsync_pid" --ppid "$rsync_pid" 2>&1
    ) || process_lines="ps failed: $process_lines"
    while IFS= read -r process_line; do
      [[ -n "$process_line" ]] && log "telemetry process: $process_line"
    done <<< "$process_lines"
    if [[ -r "/proc/$rsync_pid/io" ]]; then
      process_io=$(/usr/bin/awk \
        '/^(syscr|syscw|read_bytes|write_bytes|cancelled_write_bytes):/ { printf "%s%s=%s", separator, $1, $2; separator=" " } END { print "" }' \
        "/proc/$rsync_pid/io" 2>/dev/null) || process_io=
      [[ -z "$process_io" ]] || log "telemetry rsync_io: $process_io"
    fi
    prev_reads=$reads
    prev_read_sectors=$read_sectors
    prev_read_ms=$read_ms
    prev_writes=$writes
    prev_write_sectors=$write_sectors
    prev_write_ms=$write_ms
    prev_io_ms=$io_ms
    previous_epoch=$now
  done
}

run_snapshot_rsync() {
  local parent_name=$1 rc final_stat
  shift
  "$@" &
  child=$!
  snapshot_telemetry_loop "$child" "$parent_name" &
  telemetry_child=$!
  wait "$child"
  rc=$?
  child=
  stop_snapshot_telemetry
  if snapshot_block_stat "$parent_name"; then
    final_stat=$REPLY
    log "telemetry finished: target=$parent_name block_stat=$final_stat rsync_status=$rc"
  else
    log "telemetry finished: target=$parent_name block statistics unavailable rsync_status=$rc"
  fi
  return "$rc"
}

snapshot_parent_device() {
  local device=$1 block sys_path parent_name
  block=${device##*/}
  sys_path=$($readlink_command -f -- "$block_stat_root/$block") || return 1
  [[ -f "$block_stat_root/$block/partition" ]] || return 1
  parent_name=${sys_path%/*}
  parent_name=${parent_name##*/}
  [[ -n "$parent_name" && -b "/dev/$parent_name" ]] || return 1
  printf '/dev/%s\n' "$parent_name"
}

log_snapshot_smart() {
  local parent=$1 output rc line
  if [[ ! -x "$smartctl_command" ]]; then
    log "SMART telemetry unavailable: $smartctl_command is not executable"
    return 0
  fi
  output=$(
    /usr/bin/sudo "$timeout_command" --kill-after=2 25 \
      "$smartctl_command" -d sat -g all -H -A -l error "$parent" 2>&1
  )
  rc=$?
  log "SMART snapshot for $parent (status $rc; smartctl uses a diagnostic bitmask)"
  while IFS= read -r line; do
    case "$line" in
      "SMART overall-health"*|"SMART Health Status"*|"AAM feature is:"*|\
      "APM feature is:"*|"Rd look-ahead is:"*|"Write cache is:"*|\
      "ATA Error Count:"*|"No Errors Logged"|\
      *"Raw_Read_Error_Rate"*|*"Spin_Up_Time"*|*"Start_Stop_Count"*|\
      *"Reallocated_Sector_Ct"*|*"Power_On_Hours"*|*"Spin_Retry_Count"*|\
      *"Calibration_Retry_Count"*|*"Power_Cycle_Count"*|\
      *"G-Sense_Error_Rate"*|*"Power-Off_Retract_Count"*|\
      *"Temperature_Celsius"*|*"Reallocated_Event_Count"*|\
      *"Current_Pending_Sector"*|*"Offline_Uncorrectable"*|\
      *"UDMA_CRC_Error_Count"*|*"Load_Retry_Count"*|*"Load_Cycle_Count"*)
        log "SMART: $line"
        ;;
    esac
  done <<< "$output"
  (( rc == 124 )) && log "SMART warning: query timed out for exact active target $parent"
  return 0
}

release_job_lock() {
  (( job_locked )) || return 0
  "$flock_command" -u 9 || true
  exec 9>&-
  job_locked=0
}

stop_backup_disk() {
  local rc=0
  (( backup_disk_active )) || {
    release_job_lock
    return 0
  }
  release_job_lock
  exec 7>"$lifecycle_lock" || return 1
  if ! "$flock_command" -w 55 7; then
    log "could not acquire disk lifecycle lock to stop $EXFAT_SNAPSHOT_DISK_LABEL"
    rc=1
  elif ! "$umount_disks" --spindown "$EXFAT_SNAPSHOT_DISK_LABEL"; then
    log "could not unmount and spin down $EXFAT_SNAPSHOT_DISK_LABEL"
    rc=1
  fi
  "$flock_command" -u 7 2>/dev/null || true
  exec 7>&-
  (( rc == 0 )) && backup_disk_active=0
  return "$rc"
}

cleanup() {
  local rc=$?
  trap - EXIT TERM INT
  stop_snapshot_telemetry
  if [[ -n "$child" ]]; then
    kill -TERM "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
    child=
  fi
  if (( ! skip_exit_stop )) && ! stop_backup_disk; then
    notify "vanpi EXFAT backup cleanup FAILED" \
      "$EXFAT_SNAPSHOT_DISK_LABEL may still be mounted or spinning" \
      high warning || true
    (( rc == 0 )) && rc=1
  else
    release_job_lock
  fi
  exit "$rc"
}

aborted() {
  skip_exit_stop=1
  stop_snapshot_telemetry
  if [[ -n "$child" ]]; then
    kill -TERM "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
    child=
  fi
  log "aborted mid-snapshot (likely ignition-on)"
  notify "vanpi EXFAT backup deferred" \
    "snapshot aborted mid-run (likely ignition-on); retrying during the backup window" \
    default warning || true
  exit 143
}

trap cleanup EXIT
trap aborted TERM INT

policy_allows_hdds() {
  local output status disks torrents starlink extra
  output=$("$policyctl" read 2>&1)
  status=$?
  if (( status != 0 )); then
    log "cannot verify requested HDD policy (policyctl status $status): $output"
    return 2
  fi
  read -r disks torrents starlink extra <<< "$output"
  if [[ "$output" == *$'\n'* ||
        ! "$disks" =~ ^[01]$ || ! "$torrents" =~ ^[01]$ ||
        ! "$starlink" =~ ^[01]$ || -n ${extra:-} ]]; then
    log "cannot verify requested HDD policy: malformed policyctl output"
    return 2
  fi
  [[ "$disks" == 1 ]]
}

resolve_label() {
  local label=$1 resolved
  resolved=$("$readlink_command" -f -- "/dev/disk/by-label/$label" 2>/dev/null) ||
    return 1
  [[ -n "$resolved" && -b "$resolved" ]] || return 1
  printf '%s\n' "$resolved"
}

verify_exact_mount() {
  local label=$1 target=$2 device source resolved_source status
  device=$(resolve_label "$label") || return 1
  source=$("$findmnt_command" -rn -M "$target" -o SOURCE 2>&1)
  status=$?
  if (( status != 0 )) || [[ -z "$source" || "$source" == *$'\n'* ]]; then
    return 1
  fi
  resolved_source=$("$readlink_command" -f -- "$source" 2>/dev/null) || return 1
  [[ "$resolved_source" == "$device" ]]
}

ensure_backup_mount() {
  local output status device all_mounts
  output=$("$findmnt_command" -rn -M "$EXFAT_SNAPSHOT_MNT" -o SOURCE 2>&1)
  status=$?
  if (( status == 1 )) && [[ -z "$output" ]]; then
    "$diskctl" mount "$EXFAT_SNAPSHOT_DISK_LABEL" || return 1
  elif (( status != 0 )) || [[ -z "$output" || "$output" == *$'\n'* ]]; then
    return 1
  fi
  verify_exact_mount "$EXFAT_SNAPSHOT_DISK_LABEL" "$EXFAT_SNAPSHOT_MNT" ||
    return 1
  device=$(resolve_label "$EXFAT_SNAPSHOT_DISK_LABEL") || return 1
  all_mounts=$("$findmnt_command" -rn -S "$device" -o TARGET 2>&1)
  status=$?
  [[ $status == 0 && "$all_mounts" == "$EXFAT_SNAPSHOT_MNT" ]] || return 1
  backup_disk_active=1
  local probe="$EXFAT_SNAPSHOT_MNT/.vanpi_snapshot_probe.$$"
  "$timeout_command" 20 "$touch_command" "$probe" &&
    "$rm_command" -f -- "$probe"
}

snapshot_name_valid() {
  [[ "$1" =~ ^${EXFAT_SNAPSHOT_PREFIX}${snapshot_suffix_re}$ ]]
}

list_completed_snapshots() {
  local path name
  while IFS= read -r path; do
    name=${path##*/}
    snapshot_name_valid "$name" || continue
    [[ -d "$path" && ! -L "$path" &&
       -f "$path/$complete_marker" && ! -L "$path/$complete_marker" ]] ||
      continue
    printf '%s\n' "$name"
  done < <(
    "$find_command" "$EXFAT_SNAPSHOT_ROOT" -mindepth 1 -maxdepth 1 \
      -type d -name "${EXFAT_SNAPSHOT_PREFIX}*" -print 2>/dev/null
  )
}

snapshot_epoch() {
  local name=$1 stamp date_text
  snapshot_name_valid "$name" || return 1
  stamp=${name#"$EXFAT_SNAPSHOT_PREFIX"}
  date_text="${stamp:0:10} ${stamp:11:2}:${stamp:14:2}:00"
  "$date_command" -d "$date_text" +%s
}

delete_completed_snapshot() {
  local name=$1 path
  snapshot_name_valid "$name" || return 1
  path="$EXFAT_SNAPSHOT_ROOT/$name"
  [[ -d "$path" && ! -L "$path" &&
     -f "$path/$complete_marker" && ! -L "$path/$complete_marker" ]] ||
    return 1
  log "pruning expired hard-link snapshot $name"
  "$find_command" "$path" -depth -delete
}

prune_snapshots() {
  local now name epoch age_days bucket
  local -a snapshots=()
  local -A kept_week=()
  local -A kept_month=()
  local had_failure=0
  now=${EXFAT_SNAPSHOT_NOW:-$("$date_command" +%s)}
  [[ "$now" =~ ^[1-9][0-9]{0,10}$ ]] || return 1
  mapfile -t snapshots < <(list_completed_snapshots | /usr/bin/sort -r)
  ((${#snapshots[@]} > 1)) || return 0

  # The newest successful snapshot is never considered for pruning.
  for name in "${snapshots[@]:1}"; do
    epoch=$(snapshot_epoch "$name") || {
      had_failure=1
      continue
    }
    if (( epoch > now )); then
      continue
    fi
    age_days=$(((now - epoch) / 86400))
    if (( age_days <= EXFAT_SNAPSHOT_KEEP_DAILY_DAYS )); then
      continue
    elif (( age_days <= EXFAT_SNAPSHOT_KEEP_WEEKLY_DAYS )); then
      bucket=$("$date_command" -d "@$epoch" +%G-%V) || {
        had_failure=1
        continue
      }
      if [[ -z ${kept_week[$bucket]:-} ]]; then
        kept_week[$bucket]=1
        continue
      fi
    elif (( age_days <= EXFAT_SNAPSHOT_KEEP_MONTHLY_DAYS )); then
      bucket=$("$date_command" -d "@$epoch" +%Y-%m) || {
        had_failure=1
        continue
      }
      if [[ -z ${kept_month[$bucket]:-} ]]; then
        kept_month[$bucket]=1
        continue
      fi
    fi
    delete_completed_snapshot "$name" || had_failure=1
  done
  return "$had_failure"
}

write_success_stamp() {
  local snapshot_name=$1 temporary="$EXFAT_SNAPSHOT_STAMP.$$"
  "$install_command" -d -m 0755 -- "$STAMP_DIR" || return 1
  if ! (umask 022; printf '%s\n' "$snapshot_name" > "$temporary") ||
     ! "$mv_command" -f -- "$temporary" "$EXFAT_SNAPSHOT_STAMP"; then
    "$rm_command" -f -- "$temporary"
    return 1
  fi
}

main() {
  local policy_status source_device source_mount_status target_device target_parent
  local target_parent_name target_real root_real
  local previous_name='' previous_path='' partial_path final_name final_path
  local marker_tmp free_gb rc
  local -a completed=()
  local -a rsync_args=()

  if (( ! force )) && [[ -f "$EXFAT_SNAPSHOT_STAMP" ]] &&
     [[ "$("$date_command" -r "$EXFAT_SNAPSHOT_STAMP" +%F)" == "$("$date_command" +%F)" ]]; then
    log "EXFAT snapshot already succeeded today, nothing to do"
    return 0
  fi

  policy_allows_hdds
  policy_status=$?
  if (( policy_status == 1 )); then
    log "requested policy disables HDDs, deferring EXFAT snapshot"
    return 0
  elif (( policy_status != 0 )); then
    fail "requested HDD policy is unavailable; refusing to mount backup disk"
  fi
  if [[ -f "$IGNITION_FLAG" ]]; then
    log "van is running, deferring EXFAT snapshot"
    return 0
  fi
  acquire_job_lock || {
    log "another backup/restore is active, deferring EXFAT snapshot"
    return 0
  }
  job_locked=1
  [[ ! -f "$IGNITION_FLAG" ]] || {
    log "van started before EXFAT snapshot mount; deferring"
    return 0
  }

  source_device=$(resolve_label "$EXFAT_SNAPSHOT_SOURCE_LABEL") ||
    fail "$EXFAT_SNAPSHOT_SOURCE_LABEL is not uniquely available by exact label"
  verify_exact_mount "$EXFAT_SNAPSHOT_SOURCE_LABEL" "$EXFAT_SNAPSHOT_SOURCE_MNT" ||
    fail "$EXFAT_SNAPSHOT_SOURCE_LABEL is not mounted at its exact expected path"
  source_mount_status=$("$findmnt_command" -rn -S "$source_device" -o TARGET 2>&1)
  rc=$?
  if (( rc != 0 )) || [[ "$source_mount_status" != "$EXFAT_SNAPSHOT_SOURCE_MNT" ]]; then
    fail "$EXFAT_SNAPSHOT_SOURCE_LABEL has an unexpected or duplicate mount"
  fi

  ensure_backup_mount ||
    fail "$EXFAT_SNAPSHOT_DISK_LABEL could not be mounted and verified writable"
  [[ ! -f "$IGNITION_FLAG" ]] || {
    log "van started before snapshot copy; deferring"
    return 143
  }
  target_device=$(resolve_label "$EXFAT_SNAPSHOT_DISK_LABEL") ||
    fail "$EXFAT_SNAPSHOT_DISK_LABEL disappeared after its mount was verified"
  target_parent=$(snapshot_parent_device "$target_device") ||
    fail "could not resolve the whole-disk parent for $target_device"
  target_parent_name=${target_parent##*/}
  log_snapshot_smart "$target_parent"

  if [[ -L "$EXFAT_SNAPSHOT_ROOT" ||
        ( -e "$EXFAT_SNAPSHOT_ROOT" && ! -d "$EXFAT_SNAPSHOT_ROOT" ) ]]; then
    fail "unsafe snapshot root: $EXFAT_SNAPSHOT_ROOT"
  fi
  "$install_command" -d -m 0750 -- "$EXFAT_SNAPSHOT_ROOT" ||
    fail "could not create snapshot root"
  target_real=$("$readlink_command" -f -- "$EXFAT_SNAPSHOT_MNT") ||
    fail "could not resolve exact target mount"
  root_real=$("$readlink_command" -f -- "$EXFAT_SNAPSHOT_ROOT") ||
    fail "could not resolve snapshot root"
  [[ "$root_real" == "$target_real/backups" ]] ||
    fail "snapshot root escaped the exact target mount"

  mapfile -t completed < <(list_completed_snapshots | /usr/bin/sort -r)
  if ((${#completed[@]})); then
    previous_name=${completed[0]}
    previous_path="$EXFAT_SNAPSHOT_ROOT/$previous_name"
  fi
  partial_path="$EXFAT_SNAPSHOT_ROOT/$partial_name"
  if [[ -L "$partial_path" ||
        ( -e "$partial_path" && ! -d "$partial_path" ) ]]; then
    fail "unsafe partial snapshot path"
  fi
  "$install_command" -d -m 0750 -- "$partial_path" ||
    fail "could not prepare partial snapshot directory"

  final_name="${EXFAT_SNAPSHOT_PREFIX}$("$date_command" '+%F_%H-%M')"
  snapshot_name_valid "$final_name" || fail "generated snapshot name is invalid"
  final_path="$EXFAT_SNAPSHOT_ROOT/$final_name"
  [[ ! -e "$final_path" && ! -L "$final_path" ]] ||
    fail "snapshot destination already exists: $final_name"

  log "creating hard-link snapshot $final_name${previous_name:+ from $previous_name}"
  rsync_args=(
    -rt
    --delete-delay
    --stats
  )
  [[ -z "$previous_path" ]] ||
    rsync_args+=("--link-dest=$previous_path")
  run_snapshot_rsync "$target_parent_name" "$rsync_command" "${rsync_args[@]}" -- \
    "$EXFAT_SNAPSHOT_SOURCE_MNT/" "$partial_path/"
  rc=$?
  (( rc == 0 )) || fail "rsync snapshot exited $rc; partial snapshot retained for retry"
  [[ ! -f "$IGNITION_FLAG" ]] || return 143

  marker_tmp="$partial_path/.$complete_marker.$$"
  if ! printf 'source=%s\ndevice=%s\ncompleted=%s\nprevious=%s\n' \
      "$EXFAT_SNAPSHOT_SOURCE_LABEL" "$source_device" \
      "$("$date_command" '+%F %T')" "${previous_name:-none}" > "$marker_tmp" ||
     ! "$mv_command" -f -- "$marker_tmp" "$partial_path/$complete_marker"; then
    "$rm_command" -f -- "$marker_tmp"
    fail "could not record snapshot completion marker"
  fi
  "$sync_command" -f "$partial_path" || fail "snapshot filesystem sync failed"
  "$mv_command" -- "$partial_path" "$final_path" ||
    fail "could not atomically publish completed snapshot"
  "$sync_command" -f "$EXFAT_SNAPSHOT_ROOT" ||
    fail "snapshot directory sync failed"

  if ! prune_snapshots; then
    notify "vanpi EXFAT backup warning" \
      "snapshot $final_name succeeded, but one or more expired snapshots could not be pruned" \
      high warning || true
  fi
  free_gb=$(( $("$df_command" -k --output=avail "$EXFAT_SNAPSHOT_MNT" | /usr/bin/tail -1) / 1024 / 1024 ))
  if (( free_gb < EXFAT_SNAPSHOT_MIN_FREE_GB )); then
    notify "vanpi EXFAT backup low space" \
      "only ${free_gb}GB free on $EXFAT_SNAPSHOT_DISK_LABEL (limit ${EXFAT_SNAPSHOT_MIN_FREE_GB}GB)" \
      high warning || true
  fi

  stop_backup_disk ||
    fail "snapshot succeeded, but $EXFAT_SNAPSHOT_DISK_LABEL could not be unmounted and spun down"
  write_success_stamp "$final_name" || fail "snapshot succeeded but success stamp could not be recorded"
  log "EXFAT snapshot complete: $final_name (${free_gb}GB free)"
  if [[ "$NTFY_ON_SUCCESS" == 1 ]]; then
    notify "vanpi EXFAT backup OK" \
      "$final_name complete; ${free_gb}GB free on $EXFAT_SNAPSHOT_DISK_LABEL" \
      min white_check_mark
  fi
}

if [[ ${EXFAT_SNAPSHOT_LIBRARY_ONLY:-0} != 1 ]]; then
  main
fi
