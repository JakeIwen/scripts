#!/bin/bash
# daily backup orchestrator — replaces rsync_schedule.sh
#   1. mount + verify bigboi          4. borg create/prune (versioned history)
#   2. media mirror mp -> bigboi      5. bootable SD clones when due (CLONE_TARGETS)
#   3. HA sqlite snapshot             6. stamp + ntfy
# run as root from cron; all alerting via ntfy (see backup_conf.sh)
set -u
. /home/pi/scripts/backup_conf.sh

notify() { /home/pi/scripts/ntfy_send.sh "$@"; }
log() { echo "[$(date '+%F %T')] $*"; }
fail() { log "FATAL: $1"; notify "vanpi backup FAILED" "$1" high rotating_light; exit 1; }

exec 9>/run/pi_backup.lock
flock -n 9 || { log "another pi_backup run is active, exiting"; exit 0; }

mkdir -p "$STAMP_DIR" "$SNAP_DIR"

# mounts by label, surviving USB re-enumeration and zombie mounts (source device gone)
ensure_mounted() { # <label> <mountpoint>
  local label=$1 mnt=$2 dev src
  src=$(findmnt -no SOURCE "$mnt" 2>/dev/null)
  if [ -n "$src" ] && [ ! -b "$src" ]; then
    log "zombie mount at $mnt ($src no longer exists), detaching"
    umount -l "$mnt" 2>/dev/null
    src=""
  fi
  if [ -z "$src" ]; then
    dev=$(blkid -L "$label" 2>/dev/null || readlink -f "/dev/disk/by-label/$label" 2>/dev/null)
    [ -b "${dev:-}" ] || return 1
    mkdir -p "$mnt"
    mount "$dev" "$mnt" || return 1
  fi
  local probe="$mnt/.rw_probe_$$"
  timeout 20 touch "$probe" && rm -f "$probe"
}

# --- 1. backup disk ---
ensure_mounted "$BACKUP_DISK_LABEL" "$BACKUP_MNT" || fail "$BACKUP_DISK_LABEL not attached/writable"

# --- 2. media mirror ---
if ensure_mounted movingparts "$MEDIA_SRC"; then
  log "media mirror -> $MEDIA_DST"
  mkdir -p "$MEDIA_DST"
  rsync -aH --delete-during --delete-excluded --exclude-from="$MEDIA_EXCLUDES" \
    "$MEDIA_SRC/" "$MEDIA_DST" \
    || notify "vanpi backup" "media rsync exited $? (partial sync)" high warning
else
  notify "vanpi backup" "movingparts not available, media mirror skipped" high warning
fi

# --- 3. app-consistent snapshots (files rsync/borg could tear mid-write) ---
if [ -f "$HA_DB" ]; then
  sqlite3 "$HA_DB" ".backup '$SNAP_DIR/home-assistant_v2.db'" \
    || notify "vanpi backup" "HA sqlite snapshot failed" high warning
fi
dpkg --get-selections > "$SNAP_DIR/dpkg-selections.txt"
/srv/homeassistant/bin/pip freeze > "$SNAP_DIR/ha-pip-freeze.txt" 2>/dev/null

# --- 4. versioned history ---
archive="vanpi-$(date +%F_%H%M)"
log "borg create ::$archive"
borg create --stats --compression zstd --one-file-system \
  --exclude "$HA_DB" --exclude "$HA_DB-wal" --exclude "$HA_DB-shm" \
  --exclude /var/swap --exclude '/var/cache/apt/archives/*' \
  --exclude '/home/pi/.cache/*' --exclude '/root/.cache/*' \
  "::$archive" / /boot/firmware
rc=$?
[ $rc -le 1 ] || fail "borg create exited $rc"  # rc 1 = warnings (files changed during read), acceptable on a live system

borg prune --keep-daily "$KEEP_DAILY" --keep-weekly "$KEEP_WEEKLY" --keep-monthly "$KEEP_MONTHLY"
borg compact
if [ "$(date +%-d)" = "$BORG_CHECK_DOM" ]; then
  log "monthly borg check"
  borg check || fail "borg check found repository problems"
fi

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
    /home/pi/scripts/clone_to_sd.sh "$label" && age_days=0
  fi
  clone_summary+="$label: ${age_days}d old. "
done

# --- 6. stamp + notify ---
date '+%F %T' > "$STAMP_DIR/borg_ok"
[ "$UNMOUNT_AFTER" = 1 ] && umount "$BACKUP_MNT" 2>/dev/null
log "backup complete"
[ "$NTFY_ON_SUCCESS" = 1 ] && notify "vanpi backup OK" \
  "borg $archive done. clones: ${clone_summary:-none configured}" min white_check_mark
exit 0
