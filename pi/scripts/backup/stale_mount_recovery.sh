#!/bin/bash
# Canonical wire record for mount_disks.sh --list-stale / --recover-stale:
# one tab-separated label, exact /mnt/<label> target and /dev source per line.
# The caller supplies disk_policy_is_mount_label from disk_policy.sh.

stale_mount_parse_records() { # <records>; sets STALE_RECOVERY_LABELS / SOURCES
  local records=$1 label target source extra seen='|'
  STALE_RECOVERY_LABELS=()
  STALE_RECOVERY_SOURCES=()
  [[ -n "$records" ]] || return 0
  while IFS=$'\t' read -r label target source extra; do
    if ! disk_policy_is_mount_label "$label" || [[ "$target" != "/mnt/$label" ||
        "$source" != /dev/?* || "$source" == *[[:space:]]* || -n ${extra:-} ||
        "$seen" == *"|$label|"* ]]; then
      echo "ERROR: malformed stale-mount record; refusing recovery" >&2
      return 1
    fi
    seen+="$label|"
    STALE_RECOVERY_LABELS+=("$label")
    STALE_RECOVERY_SOURCES+=("$source")
  done <<< "$records"
}

stale_mount_recover() ( # <drain-command> <consumer-function> [original-records]
  # Both public callers keep ownership, consumer shutdown and the internal
  # unmount implementation in this shell. No trusted flag or fd handoff is needed.
  local drain=$1 consumers=$2 records library
  shift 2
  if (( $# )); then records=$1
  else records=$(md_list_stale_mounts) || return 1
  fi
  stale_mount_parse_records "$records" || return 1
  if (( ${#STALE_RECOVERY_LABELS[@]} == 0 )); then
    echo "no stale managed mounts found"
    return 0
  fi
  # Supplied records are permission, not proof of current stale state. Refuse
  # changed/live targets before signalling jobs, then recheck after the claim.
  md_prepare_stale_recovery "$records" || return 1
  library="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  . "$library/backup_locks.sh" || return 1
  . "$library/backup_lifecycle.sh" || return 1
  backup_lifecycle_claim "$drain" 0 "${STALE_RECOVERY_LABELS[@]}" || {
    echo "ERROR: backup/restore did not stop; refusing stale-mount recovery" >&2
    return 1
  }
  "$consumers" "${STALE_RECOVERY_LABELS[@]}" || return 1
  md_recover_stale_mounts "$records"
)
