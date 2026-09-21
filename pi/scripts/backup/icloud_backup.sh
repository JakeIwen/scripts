#!/bin/bash
# Reuse the existing backup lock and secret mechanism, including ignition aborts.
set -euo pipefail
source /home/pi/scripts/backup/backup_conf.sh
mode=${1:---run}
case "$mode" in --run|--preflight|--status|--login|--verify-login) ;; *) exit 2;; esac
[[ $# -le 1 ]] || exit 2
if [[ "$mode" == --run ]]; then
  acquire_job_lock || exit 0
fi
export VANPI_ICLOUD_BACKUP_MNT="$BACKUP_MNT"
export VANPI_ICLOUD_BACKUP_LABEL="$BACKUP_DISK_LABEL"
export VANPI_ICLOUD_BORG_STAMP="$STAMP_DIR/borg_ok"
export VANPI_ICLOUD_EXFAT_STAMP="$EXFAT_SNAPSHOT_STAMP"
export VANPI_ICLOUD_IGNITION_FLAG="$IGNITION_FLAG"
exec /usr/bin/python3 /home/pi/scripts/backup/icloud_backup.py "$mode"
