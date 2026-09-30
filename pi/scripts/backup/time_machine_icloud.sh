#!/bin/bash
# Share the existing backup/ignition lifecycle and private credentials.
set -euo pipefail
source /home/pi/scripts/backup/backup_conf.sh
[[ $# == 0 || $# == 1 && "$1" == --run ]] || exit 2
acquire_job_lock || exit 0
export VANPI_ICLOUD_BACKUP_MNT="$BACKUP_MNT"
export VANPI_ICLOUD_BACKUP_LABEL="$BACKUP_DISK_LABEL"
export VANPI_ICLOUD_BORG_STAMP="$STAMP_DIR/borg_ok"
export VANPI_ICLOUD_EXFAT_STAMP="$EXFAT_SNAPSHOT_STAMP"
export VANPI_ICLOUD_IGNITION_FLAG="$IGNITION_FLAG"
exec /usr/bin/python3 /home/pi/scripts/backup/time_machine_icloud.py --run
