#!/bin/bash
# Fail closed unless a Time Machine share path is the exact labeled filesystem.
# Samba runs this as the authenticated user through `preexec`; it never needs
# root privileges.
set -u

findmnt_command=${SAMBA_MOUNT_GATE_FINDMNT:-/usr/bin/findmnt}
readlink_command=${SAMBA_MOUNT_GATE_READLINK:-/usr/bin/readlink}
mount_root=${SAMBA_MOUNT_GATE_MOUNT_ROOT:-/mnt}
label_root=${SAMBA_MOUNT_GATE_LABEL_ROOT:-/dev/disk/by-label}
require_block_device=${SAMBA_MOUNT_GATE_REQUIRE_BLOCK_DEVICE:-1}

label=${1:-}
case "$label" in
  mbp1tbkup|mbp2tbkup) ;;
  *)
    echo "Time Machine mount gate: unsupported label" >&2
    exit 2
    ;;
esac

if [[ "$require_block_device" != 0 && "$require_block_device" != 1 ]]; then
  echo "Time Machine mount gate: invalid block-device setting" >&2
  exit 2
fi

target="$mount_root/$label"
label_link="$label_root/$label"

if [[ ! -d "$target" || -L "$target" ]]; then
  echo "Time Machine mount gate: $target is unavailable or unsafe" >&2
  exit 1
fi
if [[ ! -e "$label_link" ]]; then
  echo "Time Machine mount gate: exact label $label is unavailable" >&2
  exit 1
fi

source_output=$("$findmnt_command" -rn -M "$target" -o SOURCE 2>&1)
findmnt_status=$?
if (( findmnt_status != 0 )) || [[ -z "$source_output" ]]; then
  echo "Time Machine mount gate: $target is not an exact mount" >&2
  exit 1
fi
source_count=$(printf '%s\n' "$source_output" |
  /usr/bin/awk 'NF { count++ } END { print count + 0 }')
if (( source_count != 1 )); then
  echo "Time Machine mount gate: $target has ambiguous mount sources" >&2
  exit 1
fi
source_path=$(printf '%s\n' "$source_output" | /usr/bin/awk 'NF { print; exit }')

resolved_source=$("$readlink_command" -f -- "$source_path" 2>/dev/null) || {
  echo "Time Machine mount gate: cannot resolve mounted source" >&2
  exit 1
}
resolved_label=$("$readlink_command" -f -- "$label_link" 2>/dev/null) || {
  echo "Time Machine mount gate: cannot resolve exact label $label" >&2
  exit 1
}
if [[ -z "$resolved_source" || "$resolved_source" != "$resolved_label" ]]; then
  echo "Time Machine mount gate: $target is not backed by exact label $label" >&2
  exit 1
fi
if (( require_block_device )) && [[ ! -b "$resolved_source" ]]; then
  echo "Time Machine mount gate: resolved source is not a block device" >&2
  exit 1
fi

exit 0
