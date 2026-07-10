#!/bin/bash
# independent daily sanity check — alerts via ntfy when backups go stale,
# clones age out, or the backup disk is missing/full. Runs separately from
# pi_backup.sh so a dead backup job cannot fail silently (the old scheme
# failed for 2+ weeks with only broken Twilio 401s to show for it).
set -u
. /home/pi/scripts/backup/backup_conf.sh
notify() { /home/pi/scripts/ntfy_send.sh "$@"; }

problems=()
status=""
now=$(date +%s)

# borg recency
if [ -f "$STAMP_DIR/borg_ok" ]; then
  age_h=$(( (now - $(stat -c %Y "$STAMP_DIR/borg_ok")) / 3600 ))
  status+="borg: ${age_h}h ago. "
  [ "$age_h" -le "$BORG_STALE_HOURS" ] || problems+=("last good borg backup was ${age_h}h ago (limit ${BORG_STALE_HOURS}h)")
else
  problems+=("no successful borg backup recorded yet")
fi

# per-card clone recency
for entry in "${CLONE_TARGETS[@]}"; do
  label=${entry%%:*}; interval=${entry##*:}
  attached="not attached"
  blkid -t "LABEL=$label" >/dev/null 2>&1 && attached="attached"
  if [ -f "$STAMP_DIR/clone_$label" ]; then
    age_d=$(( (now - $(stat -c %Y "$STAMP_DIR/clone_$label")) / 86400 ))
    status+="$label: ${age_d}d old ($attached). "
    [ "$age_d" -le $(( interval * CLONE_STALE_FACTOR )) ] \
      || problems+=("$label clone is ${age_d}d old ($attached, interval ${interval}d)")
  else
    status+="$label: never cloned ($attached). "
    problems+=("$label has never been cloned ($attached)")
  fi
done

# all spare cards are 32GB or 64GB — warn before the system no longer fits the small ones
used_gb=$(( $(df -k --output=used / | tail -1) / 1024 / 1024 ))
status+="rootfs: ${used_gb}GB used. "
[ "$used_gb" -le "$ROOT_USED_MAX_GB" ] \
  || problems+=("rootfs holds ${used_gb}GB (limit ${ROOT_USED_MAX_GB}GB — 32GB cards may stop fitting clones)")

# backup disk presence + free space
src=$(findmnt -no SOURCE "$BACKUP_MNT" 2>/dev/null)
if [ -n "$src" ] && [ -b "$src" ]; then
  free_gb=$(( $(df -k --output=avail "$BACKUP_MNT" | tail -1) / 1024 / 1024 ))
  status+="$BACKUP_DISK_LABEL: ${free_gb}GB free."
  [ "$free_gb" -ge "$MIN_FREE_GB" ] || problems+=("only ${free_gb}GB free on $BACKUP_MNT (limit ${MIN_FREE_GB}GB)")
elif [ "$UNMOUNT_AFTER" != 1 ] && [ ! -f "$IGNITION_FLAG" ]; then
  # while the van runs, drives are unmounted on purpose — not a problem
  problems+=("$BACKUP_MNT not mounted (or zombie mount)")
fi
[ -f "$IGNITION_FLAG" ] && status+=" (van running, disk checks skipped)"

echo "[$(date '+%F %T')] $status problems: ${#problems[@]}"
if [ ${#problems[@]} -gt 0 ]; then
  notify "vanpi backup watchdog" "$(printf '%s\n' "${problems[@]}")" high rotating_light
fi
