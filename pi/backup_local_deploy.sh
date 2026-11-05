#!/bin/bash
# Shared client and remote contract for both targeted cloud installers.
local_backup_helper_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
. "$local_backup_helper_dir/scripts/backup/backup_deploy_plan.sh"
. "$local_backup_helper_dir/scripts/backup/backup_deploy_transaction.sh"

local_backup_expected_hash() { # <source:hash,...> <checkout-source>
  local expected source before after
  expected=$(printf '%s' "$1" | /usr/bin/tr ',' '\n' | /usr/bin/awk -F: -v name="$2" '$1 == name {print $2}') || return 1
  # The reviewed predecessor's minimal bootstrap edits are recorded separately;
  # this never substitutes an unrelated deployed or provider revision's hash.
  while IFS=: read -r source before after; do
    if [[ "$source" == "$2" && "$expected" == "$before" ]]; then
      expected=$after
      break
    fi
  done <<< "${LOCAL_BACKUP_BOOTSTRAP_HASHES:-}"
  printf '%s\n' "$expected"
}

local_backup_previous_hashes() { # <repo> <pi|tm>: reviewed provenance for the whole plan
  local repo=$1 profile=$2 revision=${VANPI_BACKUP_PREVIOUS_REVISION:-HEAD}
  local plan source destination policy owner mode first hash entry override
  git -C "$repo" rev-parse --verify "$revision^{commit}" >/dev/null || return 1
  plan=$(local_backup_plan "$profile") || return 1
  while IFS='|' read -r source destination policy owner mode first; do
    override=
    case "$policy" in
      conf) override=${VANPI_BACKUP_PREVIOUS_CONF_SHA256:-} ;;
      cloud) override=${VANPI_TM_PREVIOUS_CLOUD_SHA256:-} ;;
      status) override=${VANPI_TM_PREVIOUS_STATUS_SHA256:-} ;;
      replace|preserve|defaults) printf '%s:managed,' "$source"; continue ;;
    esac
    if [[ -n "$override" ]]; then
      [[ "$override" =~ ^[0-9a-f]{64}$ ]] || return 1
      hash=$override
    else
      entry=$(git -C "$repo" ls-tree "$revision" -- "$source") || return 1
      if [[ -z "$entry" ]]; then
        [[ "$first" != required ]] || return 1
        hash=absent
      else
        hash=$(git -C "$repo" show "$revision:$source" | shasum -a 256) || return 1
        hash=${hash%% *}
        [[ "$hash" =~ ^[0-9a-f]{64}$ ]] || return 1
      fi
    fi
    printf '%s:%s,' "$source" "$hash"
  done <<< "$plan"
}

local_backup_begin() { # <stage> <pi|tm> <reviewed-hashes>: fence and preflight before publication
  local stage=$1 profile=$2 previous=$3 fd timer state
  . "$local_backup_helper_dir/scripts/backup/backup_locks.sh" || return 1
  # An earlier run's interrupted transaction is restored, by its own journaled
  # recoverer, before this one can add to the mix; that recoverer takes the
  # fence itself, so it runs before this run takes it.
  backup_deploy_recover_pending $LOCAL_BACKUP_SUDO || {
    if (( $? == 75 )); then
      echo 'Backup lock is busy while an earlier deployment awaits recovery; retry deployment later.' >&2
    else
      echo 'An interrupted backup deployment could not be recovered; refusing to publish over it.' >&2
    fi
    return 1
  }
  [[ ! -L "$JOB_LOCK" && -f "$JOB_LOCK" ]] || return 1
  backup_lock_open fd "$JOB_LOCK" backup_lock_metadata_ok "$JOB_LOCK_MODE" || return 1
  exec 9<&"$fd"
  [[ "$fd" == 9 ]] || exec {fd}<&-
  "$JOB_LOCK_FLOCK" -n 9 || { echo 'Backup lock became busy; retry deployment later.' >&2; return 1; }
  LOCAL_BACKUP_STAGE=$stage
  LOCAL_BACKUP_STATE=preparing
  LOCAL_BACKUP_TEMP_ID=$BASHPID
  LOCAL_BACKUP_TXN=
  LOCAL_BACKUP_PUBLISHED=()
  LOCAL_BACKUP_TEMPORARIES=()
  LOCAL_BACKUP_ACTIVE_TIMERS=()
  umask 077
  trap 'local_backup_exit "$?"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM HUP
  # abort_backup.sh finds an exclusive legacy holder only through this record,
  # so a drain TERMs the installer into its rollback before any KILL.
  backup_lock_record_pid 9 || return 1
  if backup_deploy_pending; then
    echo 'A backup deployment transaction is still recorded; refusing to publish over it.' >&2
    return 1
  fi
  LOCAL_BACKUP_BOOTSTRAP_HASHES=$($LOCAL_BACKUP_SUDO /usr/bin/python3 -B \
    "$BACKUP_DEPLOY_BOOTSTRAP_DIR/backup_bootstrap_host.py" verify --stage "$stage") || {
    echo 'A verified guarded bootstrap is required before any cohort transaction; see CONCURRENCY.md.' >&2
    return 1
  }
  local_backup_preflight "$stage" "$profile" "$previous" || return 1
  local_backup_registry_guard "$stage" || return 1
  for timer in "${LOCAL_BACKUP_TIMERS[@]}"; do
    state=$($LOCAL_BACKUP_SUDO "$LOCAL_BACKUP_SYSTEMCTL" show "$timer" -p ActiveState --value) || return 1
    [[ "$state" != active ]] || LOCAL_BACKUP_ACTIVE_TIMERS+=("$timer")
  done
  # Recorded durably before the first live change, the timers included.
  local_backup_journal_open "$profile" || return 1
  for timer in "${LOCAL_BACKUP_ACTIVE_TIMERS[@]}"; do
    $LOCAL_BACKUP_SUDO "$LOCAL_BACKUP_SYSTEMCTL" stop "$timer" || return 1
  done
  for timer in vanpi-icloud-backup.service vanpi-time-machine-icloud.service; do
    state=$($LOCAL_BACKUP_SUDO "$LOCAL_BACKUP_SYSTEMCTL" show "$timer" -p ActiveState --value) || return 1
    case "$state" in inactive|failed) ;; *) echo "Refusing to update active worker: $timer" >&2; return 1 ;; esac
  done
  local_backup_prepare || return 1
}

local_backup_commit() { # Call only after publication and platform checks succeed.
  [[ "$LOCAL_BACKUP_STATE" == published ]] || return 1
  local_backup_verify_published || return 1
  local_backup_flush_destinations || return 1
  # A committed decision keeps its journal until timer restoration succeeds.
  # Recovery finishes it without reverting the verified new cohort.
  local_backup_marker_write committed || return 1
  LOCAL_BACKUP_STATE=committed
  printf 'Cohort committed. Rollback tree and manifest: %s/previous\n' "$LOCAL_BACKUP_TXN"
}

local_backup_exit() { # EXIT trap: rollback and timer restoration run with fd 9 still held.
  # A repeated stop or terminal disconnect must not interrupt restoration and
  # release the fence with a mixed cohort. Keep cleanup non-reentrant to exit.
  trap '' INT TERM HUP
  local status=$1 cleanup_failed=0
  trap - EXIT
  if [[ "$LOCAL_BACKUP_STATE" != committed ]]; then
    (( status != 0 )) || status=1
    if local_backup_rollback; then
      LOCAL_BACKUP_STATE=restored
      echo 'Deployment failed; previous cohort restored and verified.' >&2
    else
      LOCAL_BACKUP_STATE=incomplete
      echo "ROLLBACK INCOMPLETE: timers remain stopped and backup jobs refuse until automatic recovery (hourly) restores $LOCAL_BACKUP_TXN/previous" >&2
      status=70
    fi
  elif (( status != 0 )); then
    echo 'Cohort committed; activation incomplete. Inspect the failed action before retrying.' >&2
  fi
  local_backup_cleanup_temporaries || cleanup_failed=1
  if [[ "$LOCAL_BACKUP_STATE" == committed || "$LOCAL_BACKUP_STATE" == restored ]]; then
    local_backup_restart_timers || cleanup_failed=1
  fi
  # Anything short of a clean restore keeps the marker, so jobs keep refusing
  # and the hourly recovery finishes the job.
  if [[ "$LOCAL_BACKUP_STATE" == committed || "$LOCAL_BACKUP_STATE" == restored ]] && (( cleanup_failed == 0 )); then
    local_backup_journal_close || cleanup_failed=1
  fi
  (( cleanup_failed == 0 )) || { echo 'Deployment cleanup or timer restoration failed; automatic recovery retries it.' >&2; (( status != 0 )) || status=1; }
  exec 9<&-
  exit "$status"
}
