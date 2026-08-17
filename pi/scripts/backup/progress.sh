#!/bin/bash
# Lightweight, read-only dashboard telemetry for backup scripts.
#
# This file is sourced. Progress is advisory and must never decide whether a
# backup succeeded. Each update atomically replaces a small, world-readable
# state file; the dashboard accepts it only while the recorded script PID is
# still running with the expected argv.

backup_progress_active=0
backup_progress_file=
backup_progress_kind=
backup_progress_started_at=

backup_progress_begin() { # <borg|exfat|openwrt> <phase> <detail>
  local kind=$1 phase=$2 detail=$3
  case "$kind" in
    borg|exfat|openwrt) ;;
    *) return 1 ;;
  esac
  BACKUP_PROGRESS_DIR=${BACKUP_PROGRESS_DIR:-"${STAMP_DIR:?}/progress"}
  /usr/bin/install -d -m 0755 -- "$BACKUP_PROGRESS_DIR" || return 1
  backup_progress_kind=$kind
  backup_progress_file="$BACKUP_PROGRESS_DIR/$kind.state"
  backup_progress_started_at=$(/bin/date +%s) || return 1
  backup_progress_active=1
  backup_progress_update "$phase" "$detail"
}

backup_progress_update() { # <phase> <short human-readable detail>
  local phase=$1 detail=$2 now temporary
  (( backup_progress_active )) || return 0
  [[ "$phase" =~ ^[a-z0-9][a-z0-9_-]{0,31}$ ]] || return 1
  detail=${detail//$'\n'/ }
  detail=${detail//$'\r'/ }
  detail=${detail:0:160}
  now=$(/bin/date +%s) || return 1
  temporary="$backup_progress_file.$$"
  if ! (umask 022; /usr/bin/printf \
      'version=1\npid=%s\nstarted_at=%s\nupdated_at=%s\nphase=%s\ndetail=%s\n' \
      "$$" "$backup_progress_started_at" "$now" "$phase" "$detail" \
      > "$temporary") ||
     ! /bin/chmod 0644 "$temporary" ||
     ! /bin/mv -f -- "$temporary" "$backup_progress_file"; then
    /bin/rm -f -- "$temporary"
    return 1
  fi
}

backup_progress_end() {
  local owner=
  (( backup_progress_active )) || return 0
  if [[ -f "$backup_progress_file" ]]; then
    while IFS='=' read -r key value; do
      if [[ "$key" == pid ]]; then
        owner=$value
        break
      fi
    done < "$backup_progress_file"
    [[ "$owner" != "$$" ]] || /bin/rm -f -- "$backup_progress_file"
  fi
  backup_progress_active=0
}
