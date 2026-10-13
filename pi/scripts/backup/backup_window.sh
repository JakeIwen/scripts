#!/bin/bash
# Run every independently stamped daily backup in the existing 03:00-08:00
# retry window. A failure in one job must not prevent the other from running.
set -u

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
force_args=()

case "$#" in
  0) ;;
  1)
    if [[ "$1" == --force ]]; then
      force_args=(--force)
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

# Only this scheduled entry point may reserve a local fairness turn. Manual
# --force still uses ordinary priority admission and cannot borrow a turn.
unset VANPI_LOCAL_FAIRNESS_TOKEN
if (( ${#force_args[@]} == 0 )); then
  . "${PI_BACKUP_CONF:-/home/pi/scripts/backup/backup_conf.sh}" || exit 1
  exec /usr/bin/python3 "$script_dir/local_fairness_window.py" \
    --borg-stamp "$STAMP_DIR/borg_ok" --exfat-stamp "$EXFAT_SNAPSHOT_STAMP" \
    --stale-hours "$LOCAL_BACKUP_FAIRNESS_HOURS" \
    --admission-seconds "$LOCAL_BACKUP_FAIRNESS_ADMISSION_SECONDS" \
    --backoff-hours "$LOCAL_BACKUP_FAIRNESS_BACKOFF_HOURS"
fi

status=0
for job in pi_backup.sh exfat_snapshot.sh; do
  echo "[$(/usr/bin/date '+%F %T')] starting $job"
  "$script_dir/$job" "${force_args[@]}"
  rc=$?
  if (( rc != 0 )); then
    echo "[$(/usr/bin/date '+%F %T')] $job failed (status $rc)" >&2
    status=1
  fi
done

exit "$status"
