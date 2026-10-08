#!/bin/bash
# Share the existing backup/ignition lifecycle and private credentials.
set -euo pipefail
source /home/pi/scripts/backup/backup_conf.sh
mode=${1:---run}
[[ $# -le 1 ]] || exit 2
case "$mode" in --run|--staging-gc-plan|--staging-gc-apply) ;; *) exit 2;; esac
acquire_job_lock time-machine || { [[ "$mode" == --run ]] && exit 0; exit 1; }
export VANPI_ICLOUD_BACKUP_MNT="$BACKUP_MNT"
export VANPI_ICLOUD_BACKUP_LABEL="$BACKUP_DISK_LABEL"
export VANPI_ICLOUD_BORG_STAMP="$STAMP_DIR/borg_ok"
export VANPI_ICLOUD_EXFAT_STAMP="$EXFAT_SNAPSHOT_STAMP"
export VANPI_ICLOUD_IGNITION_FLAG="$IGNITION_FLAG"
exec /usr/bin/python3 /home/pi/scripts/backup/time_machine_icloud.py "$mode"
