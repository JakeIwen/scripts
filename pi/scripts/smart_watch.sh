#!/bin/bash
# Read SMART from the managed HDDs only while they are mounted, and send one
# ntfy alert whenever a failure signal changes. A parked disk is never queried:
# smartd's polling woke a spun-down disk behind the WD bridge on every check,
# which would spin parked drives up while driving (docs/monitoring/SMART.md).
# Each reading is also logged for the dashboard's disk graphs.
set -u

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
policy=${SMART_WATCH_POLICY:-$script_dir/disk_policy.sh}
locks=${SMART_WATCH_LOCKS:-$script_dir/backup/backup_locks.sh}
smartctl=${SMART_WATCH_SMARTCTL:-/usr/sbin/smartctl}
findmnt=${SMART_WATCH_FINDMNT:-/usr/bin/findmnt}
lsblk=${SMART_WATCH_LSBLK:-/usr/bin/lsblk}
jq=${SMART_WATCH_JQ:-/usr/bin/jq}
notify=${SMART_WATCH_NOTIFY:-$script_dir/ntfy_send.sh}
state_dir=${SMART_WATCH_STATE_DIR:-/var/lib/vanpi-smart-watch}
history_dir=$state_dir/history
history_days=30
hot_c=60   # rated maximum operating temperature of these 2.5" drives
cool_c=55  # re-arm the temperature alert once the disk cools below this

. "$policy" || exit 1
. "$locks" || exit 1
mkdir -p -m 0755 -- "$state_dir" "$history_dir" || exit 1

is_number() { [[ $1 =~ ^[0-9]+$ ]]; }

release_disk() { # <label>: drop the shared hold taken by hold_backup_disk
  local fd=${BACKUP_DISK_LOCK_FD[$1]:-}
  [[ -n $fd ]] && exec {fd}>&-
  unset "BACKUP_DISK_LOCK_FD[$1]"
}

read_smart() { # <label> <partition>: prints "passed realloc pending offline temp hours cycles"
  local label=$1 part=$2 disk report rc
  # Holding the disk lock shared keeps the policy from unmounting or spinning
  # the disk down mid-read; a lifecycle change in progress makes this skip.
  hold_backup_disk "$label" 2>/dev/null || return 2
  disk=$("$lsblk" -no PKNAME -- "$part")
  report=$("$smartctl" -j -n standby -d sat -H -A "/dev/$disk")
  rc=$?
  release_disk "$label"
  # Bit 1: the disk is in standby or could not be opened. The higher bits
  # describe disk health, which the report itself carries.
  (( rc & 3 )) && return 2
  "$jq" -r '
    def attr($id): [.ata_smart_attributes.table[]? | select(.id == $id) | .raw.value][0];
    [(.smart_status.passed), attr(5), attr(197), attr(198), (.temperature.current),
     (.power_on_time.hours), (.power_cycle_count)]
    | map(if . == null then "unknown" else tostring end) | join(" ")' <<< "$report"
}

record_history() { # <label> <values>: append "epoch values", keeping 30 days
  local file=$history_dir/$1 now first
  printf -v now '%(%s)T' -1
  # The graph is optional; a failed write must not stop the alerts.
  printf '%s %s\n' "$now" "$2" >> "$file" 2>/dev/null || return 0
  read -r first _ < "$file"
  # Trim once the oldest line is a day past the window, not on every run.
  if is_number "$first" && (( first < now - (history_days + 1) * 86400 )); then
    awk -v cutoff=$(( now - history_days * 86400 )) '$1 >= cutoff' "$file" > "$file.tmp" &&
      mv -f -- "$file.tmp" "$file"
  fi
}

check_label() { # <label>: returns 1 only when an alert could not be delivered
  local label=$1 part values passed realloc pending offline temp
  local p_passed= p_realloc= p_pending= p_offline= p_hot=0 hot state_file
  local -a alerts=()

  disk_policy_resolve_exact_label "$label" label-only
  case $? in
    0) part=$DISK_POLICY_RESOLVED_DEVICE ;;
    1) return 0 ;;
    *) echo "$label: $DISK_POLICY_RESOLVE_ERROR" >&2; return 0 ;;
  esac
  if [[ -z $("$findmnt" -rno TARGET -S "$part" 2>/dev/null) ]]; then
    echo "$label: not mounted; not queried"
    return 0
  fi
  if ! values=$(read_smart "$label" "$part"); then
    echo "$label: in standby, being unmounted, or unreadable; skipped"
    return 0
  fi
  read -r passed realloc pending offline temp _ <<< "$values"
  echo "$label: passed=$passed reallocated=$realloc pending=$pending uncorrectable=$offline temp=${temp}C"
  record_history "$label" "$values"

  state_file=$state_dir/$label
  [[ -f $state_file ]] && read -r p_passed p_realloc p_pending p_offline p_hot < "$state_file"

  [[ $passed == false && $p_passed != false ]] &&
    alerts+=("$label failed its SMART health check. Copy anything unique off it now.")
  if is_number "$realloc" && is_number "$p_realloc" && (( realloc > p_realloc )); then
    alerts+=("$label reallocated sectors rose from $p_realloc to $realloc.")
  fi
  if is_number "$pending" && (( pending > 0 )) && [[ $pending != "$p_pending" ]]; then
    alerts+=("$label has $pending pending (unreadable) sectors, previously ${p_pending:-unrecorded}.")
  fi
  if is_number "$offline" && (( offline > 0 )) && [[ $offline != "$p_offline" ]]; then
    alerts+=("$label has $offline offline-uncorrectable sectors, previously ${p_offline:-unrecorded}.")
  fi
  hot=$p_hot
  if is_number "$temp"; then
    if (( temp >= hot_c )) && [[ $p_hot != 1 ]]; then
      alerts+=("$label is at ${temp}C, at or above its ${hot_c}C limit.")
      hot=1
    elif (( temp < cool_c )); then
      hot=0
    fi
  fi

  local message
  for message in "${alerts[@]}"; do
    if ! NTFY_TOPIC_VAR=NTFY_WARNING_URL "$notify" "SMART: $label" "$message" high \
      warning,floppy_disk; then
      echo "$label: alert not delivered; state kept so the next run retries" >&2
      return 1
    fi
    echo "$label: alerted: $message"
  done
  printf '%s %s %s %s %s\n' "$passed" "$realloc" "$pending" "$offline" "$hot" \
    > "$state_file.tmp" && mv -f -- "$state_file.tmp" "$state_file"
}

status=0
for label in "${HDD_LABELS[@]}"; do
  check_label "$label" || status=1
done
exit "$status"
