#!/bin/bash
# Called with the clone job lock held. Missing peers never cause substitution.
rotation_script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
. "${HOTSPARE_ROTATION_POLICY:-$rotation_script_dir/../disk_policy.sh}" || return 1

hotspare_rotation_devices_ready() {
  local label part disk mounted status rows device mountpoint previous_disk=
  for label in "$@"; do
    if ! disk_policy_resolve_exact_label "$label" label-only; then
      log "hotspare rotation waiting: cannot safely identify $label; keeping the other card"
      return 1
    fi
    part=$DISK_POLICY_RESOLVED_DEVICE
    disk=$("${HOTSPARE_ROTATION_LSBLK:-/usr/bin/lsblk}" -dnro PKNAME "$part") || return 1
    [[ -n "$disk" && "$disk" != *$'\n'* && "$disk" != "$previous_disk" ]] || return 1
    mounted=$("${HOTSPARE_ROTATION_FINDMNT:-/usr/bin/findmnt}" -rn -S "$part")
    status=$?
    if (( status != 1 )) || [[ -n "$mounted" ]]; then
      log "hotspare rotation waiting: $label is mounted or mount discovery failed"
      return 1
    fi
    rows=$("${HOTSPARE_ROTATION_LSBLK:-/usr/bin/lsblk}" -nrpo NAME,MOUNTPOINTS -- "/dev/$disk") || return 1
    [[ -n "$rows" ]] || return 1
    while read -r device mountpoint; do
      if [[ -n "$mountpoint" ]]; then
        log "hotspare rotation waiting: a partition of $label is mounted"
        return 1
      fi
    done <<< "$rows"
    previous_disk=$disk
  done
}

run_hotspare_rotation() {
  local entry target status
  local -a labels=()
  for entry in "${CLONE_TARGETS[@]}"; do labels+=("${entry%%:*}"); done
  [[ ${#labels[@]} == 2 && "${labels[0]}" != "${labels[1]}" ]] || {
    log 'hotspare rotation requires two distinct configured cards'
    return 1
  }
  hotspare_rotation_devices_ready "${labels[@]}" || return 1
  target=$("${HOTSPARE_ROTATION_PYTHON:-/usr/bin/python3}" "$rotation_script_dir/hotspare_rotation.py" \
    target "$STAMP_DIR" "${labels[@]}" --interval "$CLONE_ROTATION_DAYS")
  status=$?
  (( status == 0 )) || return "$status"
  [[ "$target" == "${labels[0]}" || "$target" == "${labels[1]}" ]] || return 1
  backup_progress_update cloning "Refreshing bootable hotspare $target" || true
  if run "${HOTSPARE_ROTATION_CLONE:-$rotation_script_dir/clone_to_sd.sh}" "$target"; then
    clone_summary="$target refreshed; the other generation retained. "
  else
    status=$?
    clone_summary="$target unfinished; the other generation retained. "
    return "$status"
  fi
}
