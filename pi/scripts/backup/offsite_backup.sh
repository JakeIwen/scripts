#!/bin/bash
# Stable maintenance entry (units and docs call this path). prepare-disk keeps
# the exclusive legacy lock and runs live code; backup, check, receipts and
# restore-test hold only job-offsite (fd 10, PID record) and exec a frozen
# copy of their code, so installers may publish while a multi-day seed runs.
# See pi/docs/backup/CONCURRENCY.md and OFFSITE.md.
set -eu
umask 077
refuse() { printf '%s\n' "$1" >&2; exit 1; }
require_root() { (( EUID == 0 )) || refuse 'Run offsite_backup.sh with sudo.'; }
case "${1:-}" in
  dashboard|rates|pause|resume) require_root ;;
esac
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
. "$here/backup_conf.sh"
export OFFSITE_IGNITION_FLAG="$IGNITION_FLAG"
require_parked() {
  [[ ! -e "$IGNITION_FLAG" ]] || refuse 'Ignition is on; park before offsite work.'
}

hold_offsite_run() { # job identity first, then the legacy lock shared for the snapshot copy only
  local fd
  offsite_job_lock=$(backup_lock_path job offsite) || exit 1
  backup_lock_open fd "$offsite_job_lock" backup_lock_validate || exit 1
  exec 10<&"$fd"
  [[ "$fd" == 10 ]] || exec {fd}<&-
  "$JOB_LOCK_FLOCK" -n 10 || refuse 'Another offsite run is active.'
  backup_lock_record_pid 10 || exit 1
  # Restore, hotspare initialization, prepare-disk and the deployment fence
  # hold this exclusively; holding it shared while copying keeps installers
  # from publishing mid-copy, and is released before the run starts.
  backup_lock_open fd "$JOB_LOCK" validate_job_lock_file || exit 1
  exec 9<&"$fd"
  exec {fd}<&-
  "$JOB_LOCK_FLOCK" -n -s 9 ||
    refuse 'A restore, hotspare initialization, prepare-disk or backup deployment is active.'
}

case "${1:-}" in
  status|dashboard|rates|pause|resume|-h|--help) ;;
  prepare-disk)
    require_root
    acquire_job_lock || refuse 'Another backup or maintenance job is active.'
    require_parked
    ;;
  backup|check|receipts|restore-test|resume-run)
    require_root
    hold_offsite_run
    require_parked
    snapshot=$(VANPI_BACKUP_JOB_LOCK_FD=10 VANPI_BACKUP_LOCK_DIR="$BACKUP_LOCK_DIR" \
      /usr/bin/python3 -B "$here/offsite_snapshot.py" create) || exit 1
    [[ "$snapshot" == /* && "$snapshot" != *$'\n'* && -f "$snapshot/backup/offsite_backup.py" ]] ||
      refuse "Offsite code snapshot is unusable: $snapshot"
    exec 9<&-
    # Descriptor metadata for children is the run's own; never trust a caller's.
    unset VANPI_BACKUP_LEGACY_LOCK_FD VANPI_BACKUP_DISK_LOCK_FDS VANPI_BACKUP_CLOUD_SLOT_FD OFFSITE_ADMISSION
    export OFFSITE_CODE_ROOT="$snapshot" VANPI_BACKUP_JOB_KIND=offsite VANPI_BACKUP_JOB_LOCK_FD=10 \
      VANPI_BACKUP_JOB_LOCK_PATH="$offsite_job_lock" VANPI_BACKUP_LOCK_DIR="$BACKUP_LOCK_DIR"
    exec /usr/bin/python3 -B "$snapshot/backup/offsite_backup.py" "$@"
    ;;
esac
exec /usr/bin/python3 -B "$here/offsite_backup.py" "$@"
