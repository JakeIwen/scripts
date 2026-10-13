#!/bin/bash
# Share the existing backup/ignition lifecycle and private credentials.
set -euo pipefail
source /home/pi/scripts/backup/backup_conf.sh
mode=${1:---run}
[[ $# -le 1 ]] || exit 2
case "$mode" in --run|--staging-gc-plan|--staging-gc-apply) ;; *) exit 2;; esac
if [[ "$mode" == --run ]]; then
  acquire_backup_job time-machine mbp2tbkup || exit 0
  acquire_backup_lock cloud upload || exit 0
else
  acquire_backup_job time-machine mbp2tbkup || exit 1
fi
export VANPI_ICLOUD_BACKUP_MNT="$BACKUP_MNT"
export VANPI_ICLOUD_BACKUP_LABEL="$BACKUP_DISK_LABEL"
export VANPI_ICLOUD_BORG_STAMP="$STAMP_DIR/borg_ok"
export VANPI_ICLOUD_IGNITION_FLAG="$IGNITION_FLAG"
exec /usr/bin/python3 /home/pi/scripts/backup/time_machine_icloud.py "$mode"
