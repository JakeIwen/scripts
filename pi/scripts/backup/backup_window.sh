#!/bin/bash
# Run every independently stamped daily backup in the existing 03:00-08:00
# retry window. A failure in one job must not prevent the other from running.
set -u

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
force_args=()

# Root cron runs this hourly, so recovery of an interrupted backup deployment
# goes here, ahead of the window check: the mixed cohort is restored within
# the hour and before the jobs below start. While it stays recorded every job
# refuses on its own (backup_conf.sh), so a failure ends this run too.
. "$script_dir/backup_deploy_trigger.sh" || exit 1
backup_deploy_recover_pending || exit $?

case "$#" in
  0) ;;
  1)
    if [[ "$1" == --scheduled ]]; then
      /usr/bin/python3 "$script_dir/backup_settings_control.py" --window
      window_status=$?
      (( window_status != 3 )) || exit 0
      (( window_status == 0 )) || exit "$window_status"
    elif [[ "$1" == --force ]]; then
      force_args=(--force)
    else
      echo "usage: ${0##*/} [--force|--scheduled]" >&2
      exit 2
    fi
    ;;
  *)
    echo "usage: ${0##*/} [--force]" >&2
    exit 2
    ;;
esac

status=0
jobs=(pi_backup.sh exfat_snapshot.sh)
pids=()
for job in "${jobs[@]}"; do
  echo "[$(/usr/bin/date '+%F %T')] starting $job"
  "$script_dir/$job" "${force_args[@]}" &
  pids+=("$!")
done
for index in "${!jobs[@]}"; do
  job=${jobs[$index]}
  wait "${pids[$index]}"
  rc=$?
  if (( rc != 0 )); then
    echo "[$(/usr/bin/date '+%F %T')] $job failed (status $rc)" >&2
    status=1
  fi
done

exit "$status"
