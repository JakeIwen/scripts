#!/bin/bash
# Runs a recorded backup deployment's own journaled recoverer (as root, after
# checking it), and sends the warning when recovery cannot run or finish.
# Sourced by the hourly trigger (backup_window.sh), the installer before it
# takes its fence, and the recoverer itself. Second in every publication,
# right after the state module it sources, so the trigger is live early.
backup_deploy_trigger_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
. "$backup_deploy_trigger_dir/backup_deploy_state.sh" || return 1
BACKUP_DEPLOY_TRIGGER_MODULE=$backup_deploy_trigger_dir/${BASH_SOURCE[0]##*/}
# The recorded transaction's recoverer: <state dir>/<id>/recovery/<this name>.
BACKUP_DEPLOY_RECOVERY_DIR=recovery
BACKUP_DEPLOY_RECOVERY=backup_deploy_recovery.sh
# At most one warning a day while the same failure persists.
BACKUP_DEPLOY_ALERT_MINUTES=1440
# The live notifier, not a cohort file, so the journaled recoverer may name it.
BACKUP_DEPLOY_NOTIFY=${VANPI_BACKUP_DEPLOY_NOTIFY:-/home/pi/scripts/ntfy_send.sh}

backup_deploy_owned() { # <path> <d|f>: owned by the state owner, writable by nobody else
  local path=$1 kind=$2 metadata owner mode
  [[ ! -L "$path" ]] || return 1
  case "$kind" in
    d) [[ -d "$path" ]] ;;
    f) [[ -f "$path" ]] ;;
    *) false ;;
  esac || return 1
  metadata=$(/usr/bin/stat -c '%U:%G %a' -- "$path") || return 1
  read -r owner mode <<< "$metadata"
  [[ "$owner" == "$BACKUP_DEPLOY_STATE_OWNER" && "$mode" =~ ^[0-7]{3,4}$ ]] && (( (8#$mode & 022) == 0 ))
}

backup_deploy_alert() { # <message>: the warning topic, throttled while the failure persists
  local message=$1
  echo "$message" >&2
  if [[ -f "$BACKUP_DEPLOY_ALERTED" && -n $(/usr/bin/find "$BACKUP_DEPLOY_ALERTED" -maxdepth 0 \
        -mmin -"$BACKUP_DEPLOY_ALERT_MINUTES" 2>/dev/null) ]]; then
    return 0
  fi
  /usr/bin/env -u NTFY_URL NTFY_TOPIC_VAR=NTFY_WARNING_URL "$BACKUP_DEPLOY_NOTIFY" \
    'vanpi backup deployment' "$message" high warning || return 0
  : > "$BACKUP_DEPLOY_ALERTED" 2>/dev/null || true
}

backup_deploy_recover_pending() { # [privilege command ...]: recover any recorded transaction first
  backup_deploy_pending || return 0
  "$@" /bin/bash -c '. "$1" && backup_deploy_run_recovery' backup-deploy-recovery "$BACKUP_DEPLOY_TRIGGER_MODULE"
}

backup_deploy_run_recovery() { # as root: check the recorded transaction, then run its own recoverer
  local id transaction recoverer
  if ! backup_deploy_owned "$BACKUP_DEPLOY_STATE_DIR" d || ! id=$(backup_deploy_marker_id) ||
     ! backup_deploy_owned "$BACKUP_DEPLOY_MARKER" f; then
    backup_deploy_alert "Backup deployment state at $BACKUP_DEPLOY_STATE_DIR is unsafe or unreadable; backup jobs keep refusing until it is inspected."
    return 70
  fi
  transaction=$BACKUP_DEPLOY_STATE_DIR/$id
  recoverer=$transaction/$BACKUP_DEPLOY_RECOVERY_DIR/$BACKUP_DEPLOY_RECOVERY
  if ! backup_deploy_owned "$transaction" d || ! backup_deploy_owned "${recoverer%/*}" d ||
     ! backup_deploy_owned "$recoverer" f; then
    backup_deploy_alert "Backup deployment $id has no usable recovery copy in $transaction; backup jobs keep refusing until it is inspected."
    return 70
  fi
  /bin/bash "$recoverer" "$id"
}
