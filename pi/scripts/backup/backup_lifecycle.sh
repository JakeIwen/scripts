#!/bin/bash
# Shared ownership operation for disk lifecycle callers. Source backup_locks.sh
# first. Call in the shell that will mutate the disks: the retained descriptors
# must outlive consumer shutdown, unmount and (when requested) spindown.

backup_lifecycle_claim() { # <drain-command> <emergency:0|1> [label ...]
  local drain=$1 emergency=$2 label
  local -a drain_args=()
  shift 2
  [[ "$emergency" == 0 || "$emergency" == 1 ]] || return 1
  (( $# )) || return 0
  # Validate the whole set before the drain can signal any job. In particular,
  # never let an empty set become abort_backup.sh's historical "stop all".
  for label in "$@"; do
    backup_lock_path disk "$label" >/dev/null || {
      echo "unknown backup disk label: $label" >&2
      return 1
    }
  done
  (( emergency )) && drain_args=(--emergency)
  "$drain" "${drain_args[@]}" "$@" || return 1
  hold_backup_disks_exclusive "$@"
}
