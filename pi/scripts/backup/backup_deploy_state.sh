#!/bin/bash
# Durable state of backup-cohort deployments (pi/docs/backup/CONCURRENCY.md)
# and the job-admission guard that backup_conf.sh, and so every job and the
# offsite code snapshot, sources. It sources nothing and names no live code:
# it is the first record every installer publishes, so it has to work beside
# any mix of old and new files an interruption leaves.
#
# Contract kept across versions, because a newer trigger or installer must
# recover an older journal: while $BACKUP_DEPLOY_MARKER exists jobs refuse;
# its "id=" line names <state dir>/<id>, whose recovery/ holds the recoverer
# that wrote it, run as root with that id. New keys may be added; a change
# that older recoverers would misread needs a new marker version.
BACKUP_DEPLOY_STATE_DIR=${VANPI_BACKUP_DEPLOY_STATE_DIR:-/var/lib/vanpi-backup-deploy}
BACKUP_DEPLOY_STATE_OWNER=${VANPI_BACKUP_DEPLOY_STATE_OWNER:-root:root}
BACKUP_DEPLOY_MARKER=$BACKUP_DEPLOY_STATE_DIR/pending
# When the last recovery warning went out; cleared with the marker.
BACKUP_DEPLOY_ALERTED=$BACKUP_DEPLOY_STATE_DIR/alerted
BACKUP_DEPLOY_ID_PATTERN='^[0-9]{8}T[0-9]{6}Z-[1-9][0-9]*$'
BACKUP_DEPLOY_MARKER_VERSION=2
BACKUP_DEPLOY_MAINTENANCE=$BACKUP_DEPLOY_STATE_DIR/maintenance
BACKUP_DEPLOY_BOOTSTRAP_DIR=${VANPI_BACKUP_DEPLOY_BOOTSTRAP_DIR:-/usr/local/lib/vanpi-backup-deploy}

backup_deploy_pending() { # 0 while a transaction is recorded or the state cannot be inspected
  [[ -e "$BACKUP_DEPLOY_STATE_DIR" || -L "$BACKUP_DEPLOY_STATE_DIR" ]] || return 1
  [[ -d "$BACKUP_DEPLOY_STATE_DIR" && ! -L "$BACKUP_DEPLOY_STATE_DIR" && -x "$BACKUP_DEPLOY_STATE_DIR" ]] || return 0
  [[ -e "$BACKUP_DEPLOY_MARKER" || -L "$BACKUP_DEPLOY_MARKER" ]]
}

backup_deploy_marker_id() { # prints the recorded transaction id; fails when absent or unusable
  local line id=
  [[ -f "$BACKUP_DEPLOY_MARKER" && ! -L "$BACKUP_DEPLOY_MARKER" && -r "$BACKUP_DEPLOY_MARKER" ]] || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" != id=* ]] || { [[ -z "$id" ]] || return 1; id=${line#id=}; }
  done < "$BACKUP_DEPLOY_MARKER"
  [[ "$id" =~ $BACKUP_DEPLOY_ID_PATTERN ]] || return 1
  printf '%s\n' "$id"
}

backup_deploy_marker_read() { # 0 sets BACKUP_DEPLOY_{ID,PHASE,PROFILE,TEMP_ID,ROOT}; 1 absent; 2 invalid
  local line key value lines=0 version=
  local -A seen=()
  BACKUP_DEPLOY_ID= BACKUP_DEPLOY_PHASE= BACKUP_DEPLOY_PROFILE= BACKUP_DEPLOY_TEMP_ID= BACKUP_DEPLOY_ROOT=
  [[ -e "$BACKUP_DEPLOY_MARKER" || -L "$BACKUP_DEPLOY_MARKER" ]] || return 1
  [[ -f "$BACKUP_DEPLOY_MARKER" && ! -L "$BACKUP_DEPLOY_MARKER" && -r "$BACKUP_DEPLOY_MARKER" ]] || return 2
  while IFS= read -r line || [[ -n "$line" ]]; do
    (( ++lines <= 32 )) && [[ "$line" == [a-z]*=* ]] || return 2
    key=${line%%=*} value=${line#*=}
    [[ -z ${seen[$key]:-} ]] || return 2
    seen[$key]=1
    case "$key" in
      version) version=$value ;;
      id) BACKUP_DEPLOY_ID=$value ;;
      phase) BACKUP_DEPLOY_PHASE=$value ;;
      profile) BACKUP_DEPLOY_PROFILE=$value ;;
      temp) BACKUP_DEPLOY_TEMP_ID=$value ;;
      root) BACKUP_DEPLOY_ROOT=$value ;;
    esac
  done < "$BACKUP_DEPLOY_MARKER"
  [[ "$version" == "$BACKUP_DEPLOY_MARKER_VERSION" && -n ${seen[root]:-} &&
     "$BACKUP_DEPLOY_ID" =~ $BACKUP_DEPLOY_ID_PATTERN &&
     ( "$BACKUP_DEPLOY_PHASE" == preparing || "$BACKUP_DEPLOY_PHASE" == publishing || "$BACKUP_DEPLOY_PHASE" == committed ) &&
     ( "$BACKUP_DEPLOY_PROFILE" == pi || "$BACKUP_DEPLOY_PROFILE" == tm ) &&
     "$BACKUP_DEPLOY_TEMP_ID" =~ ^[1-9][0-9]*$ &&
     ( -z "$BACKUP_DEPLOY_ROOT" || ( "$BACKUP_DEPLOY_ROOT" == /* && "$BACKUP_DEPLOY_ROOT" != / ) ) ]] || return 2
}

backup_deploy_refuse_jobs() { # job admission: refuse while a deployment may have left mixed code
  local id
  if [[ -e "$BACKUP_DEPLOY_MAINTENANCE" || -L "$BACKUP_DEPLOY_MAINTENANCE" ]]; then
    echo 'Backup deployment bootstrap maintenance is held; verify and explicitly release it before starting backups.' >&2
    return 1
  fi
  backup_deploy_pending || return 0
  id=$(backup_deploy_marker_id 2>/dev/null) || id='(unreadable record)'
  echo "Backup deployment $id did not finish; backup jobs refuse until its automatic recovery restores the previous cohort (hourly, ahead of the backup window, or at the next installer run). State: $BACKUP_DEPLOY_STATE_DIR" >&2
  return 1
}
