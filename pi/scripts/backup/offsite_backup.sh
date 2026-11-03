#!/bin/bash
# Stable maintenance entry: exec preserves the PID record and exclusive fd 9.
set -eu
umask 077
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
. "$here/backup_conf.sh"
export OFFSITE_IGNITION_FLAG="$IGNITION_FLAG"
case "${1:-}" in
  status|-h|--help) ;;
  prepare-disk|backup|check|receipts|restore-test)
    (( EUID == 0 )) || { printf '%s\n' 'Run offsite_backup.sh with sudo.' >&2; exit 1; }
    acquire_job_lock || { printf '%s\n' 'Another backup or maintenance job is active.' >&2; exit 1; }
    [[ ! -e "$IGNITION_FLAG" ]] || { printf '%s\n' 'Ignition is on; park before offsite work.' >&2; exit 1; }
    ;;
esac
exec /usr/bin/python3 -B "$here/offsite_backup.py" "$@"
