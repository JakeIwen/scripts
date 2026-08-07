#!/bin/bash
# Detect one newly replaced hot-spare SD card, assign the missing configured
# hot-spare label, and initialize it with a full rpi-clone.
#
# Replace only one of the two configured cards at a time and leave the other
# attached. The surviving card supplies the expected USB-reader hardware ID;
# the missing filesystem label determines which staggered generation the new
# card replaces. Approved reader models and a narrow size comparison against
# the surviving spare distinguish the replacement from unrelated USB media.
set -u

usage() {
  cat <<'EOF'
usage: new_hotspare.sh [--dry-run] [--yes]

  --dry-run  detect and display the replacement without writing it
  --yes      skip the interactive erase confirmation

Exactly two targets must be configured in CLONE_TARGETS. Exactly one must be
attached, and exactly one unmounted replacement card must be in an approved
removable USB SD reader and within the configured size tolerance of the
attached target.
EOF
}

dry_run=0
assume_yes=0
while (( $# > 0 )); do
  case "$1" in
    --dry-run) dry_run=1 ;;
    --yes) assume_yes=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

backup_conf=${NEW_HOTSPARE_CONF:-/home/pi/scripts/backup/backup_conf.sh}
disk_policy=${NEW_HOTSPARE_DISK_POLICY:-/home/pi/scripts/disk_policy.sh}
clone_tool=${NEW_HOTSPARE_CLONE_TOOL:-/home/pi/scripts/backup/clone_to_sd.sh}
lsblk_command=${NEW_HOTSPARE_LSBLK:-/usr/bin/lsblk}
findmnt_command=${NEW_HOTSPARE_FINDMNT:-/usr/bin/findmnt}
readlink_command=${NEW_HOTSPARE_READLINK:-/usr/bin/readlink}
udevadm_command=${NEW_HOTSPARE_UDEVADM:-/usr/bin/udevadm}
timeout_command=${NEW_HOTSPARE_TIMEOUT:-/usr/bin/timeout}
require_root=${NEW_HOTSPARE_REQUIRE_ROOT:-1}
require_block_device=${NEW_HOTSPARE_REQUIRE_BLOCK_DEVICE:-1}

die() {
  echo "new_hotspare: $*" >&2
  exit 1
}

[[ -r "$backup_conf" ]] || die "cannot read backup configuration: $backup_conf"
[[ -r "$disk_policy" ]] || die "cannot read disk policy: $disk_policy"
. "$backup_conf" || die "could not load backup configuration"
. "$disk_policy" || die "could not load disk policy"

[[ "$require_root" == 0 || "$require_root" == 1 ]] ||
  die "invalid root-check setting"
[[ "$require_block_device" == 0 || "$require_block_device" == 1 ]] ||
  die "invalid block-device-check setting"
if (( ! dry_run && require_root && EUID != 0 )); then
  die "run the initializer as root (sudo $0), or use --dry-run for detection only"
fi
for command_path in \
  "$lsblk_command" "$findmnt_command" "$readlink_command" \
  "$udevadm_command" "$timeout_command"; do
  [[ -x "$command_path" ]] || die "required command is unavailable: $command_path"
done
[[ -x "$clone_tool" ]] || die "clone initializer is unavailable: $clone_tool"
declare -F disk_policy_resolve_exact_label >/dev/null ||
  die "disk policy does not provide exact-label discovery"
declare -p CLONE_TARGETS >/dev/null 2>&1 ||
  die "CLONE_TARGETS is not configured"
declare -p CLONE_USB_READER_IDS >/dev/null 2>&1 ||
  die "CLONE_USB_READER_IDS is not configured"
[[ ${CLONE_MAX_DISK_GB:-} =~ ^[1-9][0-9]{0,3}$ ]] ||
  die "CLONE_MAX_DISK_GB must be a positive integer"
[[ ${CLONE_SPARE_SIZE_TOLERANCE_GB:-} =~ ^[1-9][0-9]{0,2}$ ]] ||
  die "CLONE_SPARE_SIZE_TOLERANCE_GB must be a positive integer"

target_labels=()
for entry in "${CLONE_TARGETS[@]}"; do
  label=${entry%%:*}
  interval=${entry##*:}
  [[ "$entry" == *:* && "$label" != "$interval" &&
     "${entry#*:}" != *:* &&
     "$label" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && ${#label} -le 16 &&
     "$interval" =~ ^[1-9][0-9]*$ ]] ||
    die "invalid CLONE_TARGETS entry: $entry"
  if (( ${#target_labels[@]} > 0 )); then
    for existing_label in "${target_labels[@]}"; do
      [[ "$existing_label" != "$label" ]] ||
        die "duplicate CLONE_TARGETS label: $label"
    done
  fi
  target_labels+=("$label")
done
(( ${#target_labels[@]} == 2 )) ||
  die "expected exactly two CLONE_TARGETS, found ${#target_labels[@]}"

allowed_reader_ids=()
for signature in "${CLONE_USB_READER_IDS[@]}"; do
  [[ "$signature" =~ ^[0-9a-f]{4}:[0-9a-f]{4}$ ]] ||
    die "invalid CLONE_USB_READER_IDS entry: $signature"
  if (( ${#allowed_reader_ids[@]} > 0 )); then
    for existing_signature in "${allowed_reader_ids[@]}"; do
      [[ "$existing_signature" != "$signature" ]] ||
        die "duplicate CLONE_USB_READER_IDS entry: $signature"
    done
  fi
  allowed_reader_ids+=("$signature")
done
(( ${#allowed_reader_ids[@]} > 0 )) ||
  die "CLONE_USB_READER_IDS must not be empty"

reader_is_approved() {
  local candidate=$1 approved
  for approved in "${allowed_reader_ids[@]}"; do
    [[ "$approved" == "$candidate" ]] && return 0
  done
  return 1
}

if (( ! dry_run )); then
  declare -F acquire_job_lock >/dev/null ||
    die "backup configuration does not provide the shared job lock"
  acquire_job_lock || die "another backup, clone, or restore is active"
fi

NH_PARENT=
find_parent_disk() {
  local device=$1 ancestry name type extra
  local -a parents=()

  ancestry=$("$lsblk_command" -s -nrpo NAME,TYPE -- "$device" 2>&1) || {
    echo "cannot inspect block ancestry for $device: $ancestry" >&2
    return 1
  }
  while read -r name type extra; do
    [[ -z ${name:-} ]] && continue
    [[ -z ${extra:-} ]] || {
      echo "malformed block ancestry for $device" >&2
      return 1
    }
    [[ "$type" == disk ]] && parents+=("$name")
  done <<< "$ancestry"
  if (( ${#parents[@]} != 1 )); then
    echo "$device has ${#parents[@]} whole-disk ancestors; expected exactly one" >&2
    return 1
  fi
  NH_PARENT=${parents[0]}
}

NH_USB_SIGNATURE=
usb_reader_signature() {
  local disk=$1 properties property value vendor_id= model_id= bus=

  properties=$("$udevadm_command" info --query=property --name="$disk" 2>&1) || {
    echo "cannot inspect udev identity for $disk: $properties" >&2
    return 1
  }
  while IFS='=' read -r property value; do
    case "$property" in
      ID_BUS) bus=$value ;;
      ID_VENDOR_ID) vendor_id=$value ;;
      ID_MODEL_ID) model_id=$value ;;
    esac
  done <<< "$properties"
  [[ "$bus" == usb && "$vendor_id" =~ ^[0-9A-Fa-f]{4}$ &&
     "$model_id" =~ ^[0-9A-Fa-f]{4}$ ]] || {
    echo "incomplete USB reader identity for $disk" >&2
    return 1
  }
  [[ "$vendor_id" =~ ^[0-9a-f]{4}$ && "$model_id" =~ ^[0-9a-f]{4}$ ]] || {
    echo "USB reader identity is not normalized for $disk" >&2
    return 1
  }
  NH_USB_SIGNATURE="$vendor_id:$model_id"
}

NH_MOUNT_DETAILS=
parent_is_unmounted() {
  local disk=$1 rows name mounts extra
  rows=$("$lsblk_command" -nrpo NAME,MOUNTPOINTS -- "$disk" 2>&1) || {
    echo "cannot inspect mounts below $disk: $rows" >&2
    return 2
  }
  while read -r name mounts extra; do
    [[ -n ${name:-} ]] || continue
    if [[ -n ${mounts:-} || -n ${extra:-} ]]; then
      NH_MOUNT_DETAILS="$name is active at ${mounts:-unknown} ${extra:-}"
      return 1
    fi
  done <<< "$rows"
  return 0
}

replacement_label=
replacement_disk=
replacement_signature=
replacement_size_bytes=
discover_replacement() {
  local label status part disk root_source root_disk transport removable
  local signature attached_size_bytes disk_rows type extra size_bytes max_bytes
  local min_size_bytes max_size_bytes tolerance_bytes mount_status
  local -a present_labels=() present_disks=() missing_labels=()
  local -a candidates=() candidate_sizes=() candidate_signatures=() rejected=()

  root_source=$("$findmnt_command" -nro SOURCE / 2>&1) || {
    echo "cannot identify the live root filesystem: $root_source" >&2
    return 1
  }
  [[ -n "$root_source" && "$root_source" != *$'\n'* ]] || {
    echo "root filesystem discovery was ambiguous" >&2
    return 1
  }
  root_source=$("$readlink_command" -f -- "$root_source" 2>&1) || {
    echo "cannot resolve the live root filesystem: $root_source" >&2
    return 1
  }
  if (( require_block_device )) && [[ ! -b "$root_source" ]]; then
    echo "live root source is not a block device: $root_source" >&2
    return 1
  fi
  find_parent_disk "$root_source" || return 1
  root_disk=$NH_PARENT

  for label in "${target_labels[@]}"; do
    disk_policy_resolve_exact_label "$label" label-only
    status=$?
    if (( status == 1 )); then
      missing_labels+=("$label")
    elif (( status != 0 )); then
      echo "unsafe label mapping for $label: $DISK_POLICY_RESOLVE_ERROR" >&2
      return 1
    else
      part=$DISK_POLICY_RESOLVED_DEVICE
      if (( require_block_device )) && [[ ! -b "$part" ]]; then
        echo "resolved target is not a block device: $part" >&2
        return 1
      fi
      find_parent_disk "$part" || return 1
      disk=$NH_PARENT
      [[ "$disk" != "$root_disk" ]] || {
        echo "configured target $label is on the live root disk $root_disk" >&2
        return 1
      }
      transport=$("$lsblk_command" -dnro TRAN -- "$disk" 2>&1) || {
        echo "cannot inspect transport for $disk: $transport" >&2
        return 1
      }
      [[ "$transport" == usb ]] || {
        echo "configured target $label has non-USB parent $disk" >&2
        return 1
      }
      present_labels+=("$label")
      present_disks+=("$disk")
    fi
  done

  if (( ${#missing_labels[@]} != 1 || ${#present_labels[@]} != 1 )); then
    echo "need exactly one attached and one missing hot spare;" \
      "attached=${present_labels[*]:-none}, missing=${missing_labels[*]:-none}" >&2
    return 1
  fi

  disk=${present_disks[0]}
  removable=$("$lsblk_command" -dnro RM -- "$disk" 2>&1) || {
    echo "cannot inspect removable status for $disk: $removable" >&2
    return 1
  }
  [[ "$removable" == 1 ]] || {
    echo "attached hot spare ${present_labels[0]} is not reported as removable ($disk)" >&2
    return 1
  }
  usb_reader_signature "$disk" || return 1
  signature=$NH_USB_SIGNATURE
  reader_is_approved "$signature" || {
    echo "attached hot spare ${present_labels[0]} uses unapproved USB reader $signature" >&2
    return 1
  }
  attached_size_bytes=$("$lsblk_command" -bdnro SIZE -- "$disk" 2>&1) || {
    echo "cannot inspect attached hot-spare size for $disk: $attached_size_bytes" >&2
    return 1
  }
  [[ "$attached_size_bytes" =~ ^[1-9][0-9]*$ ]] || {
    echo "attached hot spare has no readable media size: $disk" >&2
    return 1
  }

  disk_rows=$("$lsblk_command" -dnrpo NAME,TYPE 2>&1) || {
    echo "cannot enumerate whole disks: $disk_rows" >&2
    return 1
  }
  max_bytes=$(( CLONE_MAX_DISK_GB * 1024 * 1024 * 1024 ))
  tolerance_bytes=$(( CLONE_SPARE_SIZE_TOLERANCE_GB * 1024 * 1024 * 1024 ))
  if (( attached_size_bytes > tolerance_bytes )); then
    min_size_bytes=$(( attached_size_bytes - tolerance_bytes ))
  else
    min_size_bytes=1
  fi
  max_size_bytes=$(( attached_size_bytes + tolerance_bytes ))
  while read -r disk type extra; do
    [[ -n ${disk:-} ]] || continue
    [[ -z ${extra:-} ]] || {
      echo "malformed whole-disk inventory" >&2
      return 1
    }
    [[ "$type" == disk ]] || continue
    [[ "$disk" != "$root_disk" && "$disk" != "${present_disks[0]}" ]] || continue
    if (( require_block_device )) && [[ ! -b "$disk" ]]; then
      echo "inventory contains a non-block whole disk: $disk" >&2
      return 1
    fi
    transport=$("$lsblk_command" -dnro TRAN -- "$disk" 2>&1) || {
      echo "cannot inspect transport for $disk: $transport" >&2
      return 1
    }
    [[ "$transport" == usb ]] || continue
    removable=$("$lsblk_command" -dnro RM -- "$disk" 2>&1) || {
      echo "cannot inspect removable status for $disk: $removable" >&2
      return 1
    }
    [[ "$removable" == 1 ]] || continue
    usb_reader_signature "$disk" || return 1
    if ! reader_is_approved "$NH_USB_SIGNATURE"; then
      rejected+=("$disk uses unapproved USB reader $NH_USB_SIGNATURE")
      continue
    fi

    size_bytes=$("$lsblk_command" -bdnro SIZE -- "$disk" 2>&1) || {
      echo "cannot inspect size for $disk: $size_bytes" >&2
      return 1
    }
    if [[ ! "$size_bytes" =~ ^[1-9][0-9]*$ ]]; then
      rejected+=("$disk has no readable media size")
      continue
    elif (( size_bytes > max_bytes )); then
      rejected+=("$disk exceeds the ${CLONE_MAX_DISK_GB}GB clone guard")
      continue
    elif (( size_bytes < min_size_bytes || size_bytes > max_size_bytes )); then
      rejected+=("$disk differs from the attached spare by more than ${CLONE_SPARE_SIZE_TOLERANCE_GB}GiB")
      continue
    fi
    NH_MOUNT_DETAILS=
    parent_is_unmounted "$disk"
    mount_status=$?
    if (( mount_status == 1 )); then
      rejected+=("$disk has mounted or active media ($NH_MOUNT_DETAILS)")
      continue
    elif (( mount_status != 0 )); then
      return 1
    fi
    candidates+=("$disk")
    candidate_sizes+=("$size_bytes")
    candidate_signatures+=("$NH_USB_SIGNATURE")
  done <<< "$disk_rows"

  if (( ${#candidates[@]} != 1 )); then
    echo "expected exactly one safe replacement card in the matching USB reader; found ${#candidates[@]}" >&2
    if (( ${#candidates[@]} > 0 )); then
      for disk in "${candidates[@]}"; do
        echo "  candidate: $disk" >&2
      done
    fi
    if (( ${#rejected[@]} > 0 )); then
      for extra in "${rejected[@]}"; do
        echo "  rejected: $extra" >&2
      done
    fi
    return 1
  fi

  replacement_label=${missing_labels[0]}
  replacement_disk=${candidates[0]}
  replacement_signature=${candidate_signatures[0]}
  replacement_size_bytes=${candidate_sizes[0]}
}

discover_replacement || die "replacement detection failed; no media was changed"
detected_label=$replacement_label
detected_disk=$replacement_disk
detected_signature=$replacement_signature
detected_size_bytes=$replacement_size_bytes

echo "Detected missing hot-spare generation: $detected_label"
echo "Detected replacement card: $detected_disk ($((detected_size_bytes / 1024 / 1024 / 1024)) GiB)"
echo "Approved replacement USB reader: $detected_signature"
echo "Current card layout (all contents will be destroyed):"
"$lsblk_command" -o NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINTS -- "$detected_disk" ||
  die "could not display the replacement card layout"

if (( dry_run )); then
  echo "Dry run only; the card was not changed."
  exit 0
fi

if (( ! assume_yes )); then
  [[ -t 0 ]] || die "interactive confirmation requires a terminal; rerun with --yes only after reviewing --dry-run"
  confirmation="ERASE $detected_disk AS $detected_label"
  printf "Type '%s' to continue: " "$confirmation"
  IFS= read -r answer
  [[ "$answer" == "$confirmation" ]] || {
    echo "Canceled; the card was not changed."
    exit 4
  }
fi

# Re-run every discovery check after confirmation so a removed/re-enumerated
# device cannot silently redirect the destructive initializer.
discover_replacement || die "replacement changed during confirmation; no clone was started"
[[ "$replacement_label" == "$detected_label" &&
   "$replacement_disk" == "$detected_disk" &&
   "$replacement_signature" == "$detected_signature" &&
   "$replacement_size_bytes" == "$detected_size_bytes" ]] ||
  die "replacement identity changed during confirmation; no clone was started"

echo "Initializing $detected_disk as $detected_label with a full Pi clone..."
child=
stop_clone() {
  if [[ -n "$child" ]]; then
    kill -TERM "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
  fi
  exit 143
}
trap stop_clone TERM INT

"$timeout_command" --signal=TERM --kill-after=60 6h \
  "$clone_tool" --init "$detected_label" "${detected_disk#/dev/}" &
child=$!
wait "$child"
status=$?
child=
(( status == 0 )) || die "clone initializer failed with status $status"

echo "$detected_label is initialized, labeled, stamped, and ready as the replacement hot spare."
exit 0
