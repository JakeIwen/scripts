#!/bin/bash
# Restore one interrupted backup-cohort publication from its durable journal,
# under the deployment fence, then restart the timers it stopped and clear its
# marker. Before any live change the installer copies this file and every
# library it sources into the transaction's recovery/ directory
# (local_backup_journal_open), and backup_deploy_run_recovery runs that copy:
# the code reading a journal is the code that wrote it, whatever mix of old
# and new files the interruption left in the live tree.
#
# Exit status: 0 recovered (or nothing recorded), 75 fence busy (retry later,
# no alert), 70 incomplete (marker kept so jobs keep refusing, timers stay
# stopped, warning sent).
set -u
recovery_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# Root runs the journaled commands directly; tests pass their sudo shim.
(( EUID != 0 )) || LOCAL_BACKUP_SUDO=
for library in backup_deploy_trigger.sh backup_deploy_plan.sh backup_deploy_transaction.sh backup_locks.sh; do
  . "$recovery_dir/$library" || exit 70
done

recovery_fail() { # <message>
  backup_deploy_alert "RECOVERY INCOMPLETE for backup deployment ${BACKUP_DEPLOY_ID:-$1}: $2 Backup jobs keep refusing and its timers stay stopped; it is retried hourly. Journal: $BACKUP_DEPLOY_STATE_DIR/${BACKUP_DEPLOY_ID:-$1}"
  exit 70
}

recovery_fence() { # exclusive legacy lock with a PID record, so drains TERM this process
  local fd
  [[ ! -L "$JOB_LOCK" && -f "$JOB_LOCK" ]] || return 1
  backup_lock_open fd "$JOB_LOCK" backup_lock_metadata_ok "$JOB_LOCK_MODE" || return 1
  exec 9<&"$fd"
  [[ "$fd" == 9 ]] || exec {fd}<&-
  "$JOB_LOCK_FLOCK" -n 9 || { echo 'Backup lock is busy; deployment recovery retries later.' >&2; exit 75; }
  backup_lock_record_pid 9
}

recovery_record_ok() { # <record>: one plan destination, well-formed fields, its fixed temporary
  local source destination old_hash old_owner old_mode new_hash owner mode action temporary extra
  IFS='|' read -r source destination old_hash old_owner old_mode new_hash owner mode action temporary extra <<< "$1"
  [[ -z "$extra" && -n "$destination" && "${planned_source[$destination]:-}" == "$source" &&
     -z ${journaled[$destination]:-} ]] || return 1
  journaled[$destination]=1
  [[ ( "$old_hash" == absent && "$old_owner" == - && "$old_mode" == - ) ||
     ( "$old_hash" =~ ^[0-9a-f]{64}$ && "$old_owner" =~ ^[^:]+:[^:]+$ && "$old_mode" =~ ^[0-7]{3,4}$ ) ]] &&
    [[ "$new_hash" =~ ^[0-9a-f]{64}$ && "$owner" =~ ^[^:]+:[^:]+$ && "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
  case "$action" in
    replace) [[ "$temporary" == "$LOCAL_BACKUP_ROOT$destination.deploy.$LOCAL_BACKUP_TEMP_ID" ]] || return 1
      LOCAL_BACKUP_PUBLISHED+=("$1") ;;
    keep) [[ "$temporary" == - ]] ;;
    *) return 1 ;;
  esac
}

recovery_load() { # validated timers, stale temporaries and (once publishing began) the full manifest
  local timer record source destination rest plan
  local -A planned_source=() journaled=()
  LOCAL_BACKUP_ROOT=$BACKUP_DEPLOY_ROOT
  LOCAL_BACKUP_TXN=$BACKUP_DEPLOY_STATE_DIR/$BACKUP_DEPLOY_ID
  LOCAL_BACKUP_TEMP_ID=$BACKUP_DEPLOY_TEMP_ID
  LOCAL_BACKUP_TEMPORARIES=("$BACKUP_DEPLOY_MARKER.$LOCAL_BACKUP_TEMP_ID")
  LOCAL_BACKUP_PUBLISHED=() LOCAL_BACKUP_ACTIVE_TIMERS=()
  while IFS= read -r timer; do
    [[ -n "$timer" ]] || continue
    backup_lock_known "$timer" "${LOCAL_BACKUP_TIMERS[@]}" || return 1
    LOCAL_BACKUP_ACTIVE_TIMERS+=("$timer")
  done < <($LOCAL_BACKUP_SUDO /bin/cat -- "$LOCAL_BACKUP_TXN/active-timers" || echo '!unreadable')
  plan=$(local_backup_plan "$BACKUP_DEPLOY_PROFILE") || return 1
  # A killed run may not have recorded its temporaries; their names are fixed.
  while IFS='|' read -r source destination rest; do
    planned_source[$destination]=$source
    LOCAL_BACKUP_TEMPORARIES+=("$LOCAL_BACKUP_ROOT$destination.deploy.$LOCAL_BACKUP_TEMP_ID"
      "$LOCAL_BACKUP_ROOT$destination.rollback.$LOCAL_BACKUP_TEMP_ID")
  done <<< "$plan"
  [[ "$BACKUP_DEPLOY_PHASE" == publishing ]] || return 0
  while IFS= read -r record; do
    recovery_record_ok "$record" || return 1
  done < <($LOCAL_BACKUP_SUDO /bin/cat -- "$LOCAL_BACKUP_TXN/previous/manifest" || echo '!unreadable')
  # The marker turns to publishing only once the whole manifest is durable.
  (( ${#journaled[@]} == ${#planned_source[@]} ))
}

main() { # <transaction id>
  local id=$1 status
  trap 'exit 143' TERM INT HUP
  [[ "$id" =~ $BACKUP_DEPLOY_ID_PATTERN && "$recovery_dir" == "$BACKUP_DEPLOY_STATE_DIR/$id/$BACKUP_DEPLOY_RECOVERY_DIR" ]] ||
    recovery_fail "$id" 'this recoverer belongs to another transaction.'
  recovery_fence || recovery_fail "$id" 'the deployment fence (legacy backup lock) is unsafe.'
  # Interruption is safe from here on: every step below is idempotent and
  # the marker stays until all of them succeed, so a drain may TERM this run.
  backup_deploy_marker_read
  status=$?
  (( status != 1 )) || { echo "Backup deployment $id needs no recovery."; exit 0; }
  (( status == 0 )) || recovery_fail "$id" 'its marker is unreadable or invalid.'
  [[ "$BACKUP_DEPLOY_ID" == "$id" ]] || { echo "Another deployment is recorded now; retrying later." >&2; exit 75; }
  recovery_load || recovery_fail "$id" 'its journal is incomplete or names paths outside the deployment plan.'
  local_backup_cleanup_temporaries || recovery_fail "$id" 'a leftover temporary file is not a regular file.'
  LOCAL_BACKUP_TEMPORARIES=()
  local_backup_rollback || recovery_fail "$id" 'a live file matches neither its previous nor its published version, or systemd could not reload.'
  local_backup_cleanup_temporaries || recovery_fail "$id" 'a rollback temporary could not be removed.'
  local_backup_restart_timers || recovery_fail "$id" 'the previous cohort is restored but a timer did not start.'
  local_backup_journal_close || recovery_fail "$id" 'the previous cohort is restored but its marker could not be removed.'
  echo "Recovered interrupted backup deployment $id: previous cohort restored and verified; timers restarted."
}

main "$@"
