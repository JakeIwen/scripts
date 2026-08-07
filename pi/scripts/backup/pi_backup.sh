#!/bin/bash
# daily backup orchestrator — replaces rsync_schedule.sh
#   1. mount + verify bigboi          4. borg create/prune (versioned history)
#   2. media mirror mp -> bigboi      5. bootable SD clones when due (CLONE_TARGETS)
#   3. HA + router snapshots          6. stamp + ntfy
# cron fires this hourly 03:00-08:00 (when the van is least likely to drive);
# the first success of the day wins and later runs no-op. Defers while the van
# runs (drives are unmounted for vibration protection); if the van starts
# mid-run, umount_disks.sh -> abort_backup.sh TERMs us and we stop cleanly
# before the bounded emergency unmount.
set -u
backup_conf=${PI_BACKUP_CONF:-/home/pi/scripts/backup/backup_conf.sh}
policyctl=${PI_BACKUP_POLICYCTL:-/home/pi/scripts/policyctl}
mount_disks=${PI_BACKUP_MOUNT_DISKS:-/home/pi/scripts/mount_disks.sh}
. "$backup_conf"

notify() { /home/pi/scripts/ntfy_send.sh "$@"; }
log() { echo "[$(date '+%F %T')] $*"; }
fail() { log "FATAL: $1"; notify "vanpi backup FAILED" "$1" high rotating_light; exit 1; }

force=0
case "$#" in
  0) ;;
  1)
    if [[ "$1" == --force ]]; then
      # A deliberate manual run overrides the daily success short-circuit.
      # Requested HDD policy and ignition remain authoritative.
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

if [ $force = 0 ] && [ -f "$STAMP_DIR/borg_ok" ] && [ "$(date -r "$STAMP_DIR/borg_ok" +%F)" = "$(date +%F)" ]; then
  log "already succeeded today, nothing to do"
  exit 0
fi

backup_policy_allows_hdds() {
  local output status disks torrents starlink extra

  if [[ ! -x "$policyctl" ]]; then
    log "cannot verify requested HDD policy: $policyctl is unavailable"
    return 2
  fi
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

backup_policy_allows_hdds
policy_status=$?
if (( policy_status == 1 )); then
  log "requested policy disables HDDs, deferring backup without mounting disks"
  exit 0
elif (( policy_status != 0 )); then
  log "requested HDD policy is unavailable, refusing to mount backup disks"
  exit 1
fi

if [ -f "$IGNITION_FLAG" ]; then
  log "van is running, deferring (drives unmounted for vibration protection)"
  exit 0
fi
acquire_job_lock || { log "another backup/restore is active, exiting"; exit 0; }

# long steps go through run() so a TERM from abort_backup.sh stops them promptly
# (bash delays traps until the foreground child exits; wait doesn't)
child=
run() { "$@" & child=$!; wait "$child"; local rc=$?; child=; return $rc; }
aborted() {
  [ -n "$child" ] && { kill -TERM "$child" 2>/dev/null; wait "$child" 2>/dev/null; }
  log "aborted mid-run (van started?)"
  notify "vanpi backup deferred" "aborted mid-run (likely ignition-on); retrying hourly until 08:00" default warning
  exit 143
}
trap aborted TERM INT
bail_if_driving() {
  [ -f "$IGNITION_FLAG" ] || return 0
  log "van started mid-run, stopping before the next phase"
  notify "vanpi backup deferred" "van started mid-run; retrying hourly until 08:00" default warning
  exit 143
}

mkdir -p "$STAMP_DIR" "$SNAP_DIR"

# Use the shared exact-label mount implementation; it owns stale-source,
# underlay, root-disk, transport, and filesystem validation.
ENSURE_MOUNTED_DID_MOUNT=0
ensure_mounted() { # <label> <mountpoint>
  local label=$1 mnt=$2 src resolved_source resolved_label status
  ENSURE_MOUNTED_DID_MOUNT=0

  src=$(/usr/bin/findmnt -rn -M "$mnt" -o SOURCE 2>&1)
  status=$?
  if (( status == 1 )) && [[ -z "$src" ]]; then
    "$mount_disks" "$label" || return 1
    ENSURE_MOUNTED_DID_MOUNT=1
  elif (( status != 0 )) || [[ -z "$src" || "$src" == *$'\n'* ]]; then
    log "cannot verify the exact mount at $mnt (findmnt status $status): ${src:-no output}"
    return 1
  fi

  src=$(/usr/bin/findmnt -rn -M "$mnt" -o SOURCE 2>&1)
  status=$?
  if (( status != 0 )) || [[ -z "$src" || "$src" == *$'\n'* ]]; then
    log "$mount_disks did not establish one exact mount at $mnt"
    return 1
  fi
  resolved_source=$(/usr/bin/readlink -f -- "$src" 2>/dev/null) || return 1
  resolved_label=$(/usr/bin/readlink -f -- "/dev/disk/by-label/$label" 2>/dev/null) ||
    return 1
  if [[ -z "$resolved_source" || "$resolved_source" != "$resolved_label" ||
        ! -b "$resolved_source" ]]; then
    log "refusing $mnt because it is not backed by exact label $label"
    return 1
  fi

  local probe="$mnt/.rw_probe_$$"
  /usr/bin/timeout 20 /usr/bin/touch "$probe" && /usr/bin/rm -f -- "$probe"
}

# --- 1. backup disk ---
ensure_mounted "$BACKUP_DISK_LABEL" "$BACKUP_MNT" || fail "$BACKUP_DISK_LABEL not attached/writable"
backup_mounted_here=$ENSURE_MOUNTED_DID_MOUNT

# --- 2. media mirror ---
if ensure_mounted movingparts "$MEDIA_SRC"; then
  log "media mirror -> $MEDIA_DST"
  mkdir -p "$MEDIA_DST"
  run rsync -aH --delete-during --delete-excluded --exclude-from="$MEDIA_EXCLUDES" \
    "$MEDIA_SRC/" "$MEDIA_DST" \
    || notify "vanpi backup" "media rsync exited $? (partial sync)" high warning
else
  notify "vanpi backup" "movingparts not available, media mirror skipped" high warning
fi

bail_if_driving

# --- 3. app-consistent snapshots (files rsync/borg could tear mid-write) ---
if [ -f "$HA_DB" ]; then
  sqlite3 "$HA_DB" ".backup '$SNAP_DIR/home-assistant_v2.db'" \
    || notify "vanpi backup" "HA sqlite snapshot failed" high warning
fi
dpkg --get-selections > "$SNAP_DIR/dpkg-selections.txt"
/srv/homeassistant/bin/pip freeze > "$SNAP_DIR/ha-pip-freeze.txt" 2>/dev/null

log "OpenWrt router snapshot"
if run /home/pi/scripts/backup/openwrt_backup.sh; then
  log "OpenWrt router snapshot verified"
else
  rc=$?
  notify "vanpi backup" \
    "OpenWrt snapshot failed (status $rc); Borg will retain the last valid router snapshot" \
    high warning
fi

log "UBNT persistent snapshot"
if run /home/pi/scripts/backup/ubnt_backup.sh; then
  log "UBNT persistent snapshot verified"
else
  rc=$?
  notify "vanpi backup" \
    "UBNT snapshot failed (status $rc); Borg will retain the last valid antenna snapshot" \
    high warning
fi

# --- 4. versioned history ---
archive="vanpi-$(date +%F_%H%M)"
borg_exclude_args=()
for exclude in "${BORG_EXCLUDES[@]}"; do
  borg_exclude_args+=(--exclude "$exclude")
done
log "borg create ::$archive"
run borg create --stats --compression zstd --one-file-system \
  "${borg_exclude_args[@]}" \
  "::$archive" / /boot/firmware
rc=$?
[ $rc -le 1 ] || fail "borg create exited $rc"  # rc 1 = warnings (files changed during read), acceptable on a live system

run borg prune --keep-daily "$KEEP_DAILY" --keep-weekly "$KEEP_WEEKLY" --keep-monthly "$KEEP_MONTHLY"
run borg compact
if [ "$(date +%-d)" = "$BORG_CHECK_DOM" ]; then
  log "monthly borg check"
  run borg check || fail "borg check found repository problems"
fi

bail_if_driving

# --- 5. bootable clones when due ---
clone_summary=""
now=$(date +%s)
for entry in "${CLONE_TARGETS[@]}"; do
  label=${entry%%:*}; interval=${entry##*:}
  stamp="$STAMP_DIR/clone_$label"
  age_days=9999
  [ -f "$stamp" ] && age_days=$(( (now - $(stat -c %Y "$stamp")) / 86400 ))
  if [ "$age_days" -ge "$interval" ]; then
    log "clone to $label due (${age_days}d >= ${interval}d)"
    run /home/pi/scripts/backup/clone_to_sd.sh "$label" && age_days=0
  fi
  clone_summary+="$label: ${age_days}d old. "
done

# --- 6. stamp + notify ---
date '+%F %T' > "$STAMP_DIR/borg_ok"
# check free space here, while bigboi is guaranteed mounted (watchdog can't when UNMOUNT_AFTER=1)
free_gb=$(( $(df -k --output=avail "$BACKUP_MNT" | tail -1) / 1024 / 1024 ))
[ "$free_gb" -ge "$MIN_FREE_GB" ] || notify "vanpi backup" \
  "only ${free_gb}GB free on $BACKUP_MNT (limit ${MIN_FREE_GB}GB)" high warning
if [ "$UNMOUNT_AFTER" = 1 ]; then
  if [ "$backup_mounted_here" = 1 ]; then
    if ! umount "$BACKUP_MNT"; then
      log "WARNING: could not unmount $BACKUP_MNT (an open file or shell may be holding it)"
      notify "vanpi backup" \
        "backup succeeded, but $BACKUP_MNT could not be unmounted; check for an open file or shell before moving the van" \
        high warning
    fi
  else
    log "leaving pre-existing $BACKUP_MNT mount in place"
  fi
fi
log "backup complete (${free_gb}GB free on $BACKUP_DISK_LABEL)"
[ "$NTFY_ON_SUCCESS" = 1 ] && notify "vanpi backup OK" \
  "borg $archive done, ${free_gb}GB free. clones: ${clone_summary:-none configured}" min white_check_mark
exit 0
