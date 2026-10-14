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
