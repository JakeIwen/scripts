#!/bin/bash
# Shared client and remote contract for both targeted cloud installers.
local_backup_helper_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
. "$local_backup_helper_dir/scripts/backup/backup_deploy_plan.sh"
. "$local_backup_helper_dir/scripts/backup/backup_deploy_transaction.sh"

local_backup_expected_hash() { # <source:hash,...> <checkout-source>
  printf '%s' "$1" | /usr/bin/tr ',' '\n' | /usr/bin/awk -F: -v name="$2" '$1 == name {print $2}'
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
  [[ ! -L "$JOB_LOCK" && -f "$JOB_LOCK" ]] || return 1
  backup_lock_open fd "$JOB_LOCK" backup_lock_metadata_ok "$JOB_LOCK_MODE" || return 1
  exec 9<&"$fd"
  [[ "$fd" == 9 ]] || exec {fd}<&-
  "$JOB_LOCK_FLOCK" -n 9 || { echo 'Backup lock became busy; retry deployment later.' >&2; return 1; }
  LOCAL_BACKUP_STAGE=$stage
  LOCAL_BACKUP_STATE=preparing
  LOCAL_BACKUP_PUBLISHED=()
  LOCAL_BACKUP_TEMPORARIES=()
  LOCAL_BACKUP_ACTIVE_TIMERS=()
  umask 077
  trap 'local_backup_exit "$?"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM HUP
  local_backup_preflight "$stage" "$profile" "$previous" || return 1
  for timer in "${LOCAL_BACKUP_TIMERS[@]}"; do
    state=$($LOCAL_BACKUP_SUDO "$LOCAL_BACKUP_SYSTEMCTL" show "$timer" -p ActiveState --value) || return 1
    [[ "$state" != active ]] || LOCAL_BACKUP_ACTIVE_TIMERS+=("$timer")
  done
  printf '%s\n' "${LOCAL_BACKUP_ACTIVE_TIMERS[@]}" > "$stage/transaction/active-timers" || return 1
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
  LOCAL_BACKUP_STATE=committed
  printf 'Cohort committed. Rollback tree and manifest: %s/previous\n' "$LOCAL_BACKUP_STAGE"
}

local_backup_exit() { # EXIT trap: rollback and timer restoration run with fd 9 still held.
  local status=$1 timer cleanup_failed=0
  trap - EXIT INT TERM HUP
  if [[ "$LOCAL_BACKUP_STATE" != committed ]]; then
    (( status != 0 )) || status=1
    if local_backup_rollback; then
      LOCAL_BACKUP_STATE=restored
      echo 'Deployment failed; previous cohort restored and verified.' >&2
    else
      LOCAL_BACKUP_STATE=incomplete
      echo "ROLLBACK INCOMPLETE: timers remain stopped. Recovery: $LOCAL_BACKUP_STAGE/previous" >&2
      status=70
    fi
  elif (( status != 0 )); then
    echo 'Cohort committed; activation incomplete. Inspect the failed action before retrying.' >&2
  fi
  local_backup_cleanup_temporaries || cleanup_failed=1
  if [[ "$LOCAL_BACKUP_STATE" == committed || "$LOCAL_BACKUP_STATE" == restored ]]; then
    for timer in "${LOCAL_BACKUP_ACTIVE_TIMERS[@]}"; do
      $LOCAL_BACKUP_SUDO "$LOCAL_BACKUP_SYSTEMCTL" start "$timer" || cleanup_failed=1
    done
  fi
  (( cleanup_failed == 0 )) || { echo 'Deployment cleanup or timer restoration failed.' >&2; (( status != 0 )) || status=1; }
  exec 9<&-
  exit "$status"
}
