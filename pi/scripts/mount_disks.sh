#! /bin/bash

# Set by md_resolve_label on success.
MD_DEVICE=""
MD_LABEL_KEY=""

md_query_token() {
  local token="$1"
  local devices
  local status

  devices="$(/usr/bin/sudo /sbin/blkid -t "$token" -o device)"
  status=$?
  if (( status == 0 )); then
    if [[ -z "$devices" ]]; then
      echo "ERROR: blkid succeeded but returned no device for $token" >&2
      return 2
    fi
    printf '%s\n' "$devices"
    return 0
  fi

  # blkid returns 2 when no device matches a token.
  if (( status == 2 )) && [[ -z "$devices" ]]; then
    return 1
  fi

  echo "ERROR: blkid failed while resolving $token (status $status)" >&2
  return 2
}

md_resolve_label() {
  local label="$1"
  local label_devices
  local label_status
  local partlabel_devices
  local partlabel_status
  local devices
  local device
  local count
  local candidate
  local actual_label

  MD_DEVICE=""
  MD_LABEL_KEY=""
  if [[ -z "$label" ]]; then
    echo "ERROR: refusing to resolve an empty disk label" >&2
    return 2
  fi

  label_devices="$(md_query_token "LABEL=$label")"
  label_status=$?
  (( label_status == 2 )) && return 2

  partlabel_devices="$(md_query_token "PARTLABEL=$label")"
  partlabel_status=$?
  (( partlabel_status == 2 )) && return 2

  if (( label_status == 1 && partlabel_status == 1 )); then
    return 1
  fi

  devices="$({ printf '%s\n' "$label_devices"; printf '%s\n' "$partlabel_devices"; } \
    | /usr/bin/awk 'NF && !seen[$0]++')"
  count="$(printf '%s\n' "$devices" | /usr/bin/awk 'NF { count++ } END { print count + 0 }')"
  if (( count != 1 )); then
    echo "ERROR: exact label '$label' resolves to $count devices; refusing" >&2
    printf '%s\n' "$devices" >&2
    return 2
  fi

  device="$(printf '%s\n' "$devices" | /usr/bin/awk 'NF { print; exit }')"
  device="$(/usr/bin/readlink -f -- "$device")"
  if [[ -z "$device" || ! -b "$device" ]]; then
    echo "ERROR: resolved device for '$label' is not a block device" >&2
    return 2
  fi

  MD_LABEL_KEY="PARTLABEL"
  while IFS= read -r candidate; do
    if [[ -n "$candidate" && "$(/usr/bin/readlink -f -- "$candidate")" == "$device" ]]; then
      MD_LABEL_KEY="LABEL"
      break
    fi
  done <<< "$label_devices"

  actual_label="$(/usr/bin/sudo /sbin/blkid -s "$MD_LABEL_KEY" -o value -- "$device")"
  if [[ $? -ne 0 || "$actual_label" != "$label" ]]; then
    echo "ERROR: $device no longer has exact $MD_LABEL_KEY '$label'; refusing" >&2
    return 2
  fi

  MD_DEVICE="$device"
  return 0
}

md_parent_disk() {
  local device="$1"
  local ancestors
  local status
  local disks
  local count

  ancestors="$(/usr/bin/lsblk -s -nrpo NAME,TYPE -- "$device")"
  status=$?
  if (( status != 0 )) || [[ -z "$ancestors" ]]; then
    echo "ERROR: cannot determine parent disk for $device" >&2
    return 1
  fi

  disks="$(printf '%s\n' "$ancestors" \
    | /usr/bin/awk '$2 == "disk" && !seen[$1]++ { print $1 }')"
  count="$(printf '%s\n' "$disks" | /usr/bin/awk 'NF { count++ } END { print count + 0 }')"
  if (( count != 1 )); then
    echo "ERROR: expected one parent disk for $device, found $count" >&2
    return 1
  fi

  printf '%s\n' "$disks"
}

mntdsk() {
  local label="$1"
  local pth="/mnt/$label"
  local resolve_status
  local device
  local label_key
  local mount_targets
  local read_only
  local fstype
  local root_source
  local root_disk
  local device_disk
  local actual_label
  local mounted_target
  local -a mount_opts=()

  md_resolve_label "$label"
  resolve_status=$?
  if (( resolve_status == 1 )); then
    echo "no exact LABEL or PARTLABEL match for: $label"
    return 0
  elif (( resolve_status != 0 )); then
    return 1
  fi
  device="$MD_DEVICE"
  label_key="$MD_LABEL_KEY"

  mount_targets="$(/usr/bin/findmnt -rn -S "$device" -o TARGET)"
  if [[ -n "$mount_targets" ]]; then
    if [[ "$mount_targets" == "$pth" ]]; then
      echo "already mounted for: $label ($device)"
      return 0
    fi
    echo "ERROR: $device is already mounted somewhere other than $pth: $mount_targets" >&2
    return 1
  fi

  read_only="$(/usr/bin/lsblk -dnro RO -- "$device")"
  if [[ $? -ne 0 || ! "$read_only" =~ ^[01]$ ]]; then
    echo "ERROR: cannot determine read-only state for $device" >&2
    return 1
  elif [[ "$read_only" == "1" ]]; then
    echo "ERROR: refusing to mount read-only device for: $label ($device)" >&2
    return 1
  fi

  root_source="$(/usr/bin/findmnt -rn -T / -o SOURCE)"
  if [[ $? -ne 0 || -z "$root_source" ]]; then
    echo "ERROR: cannot determine root filesystem source; refusing to mount $label" >&2
    return 1
  fi
  root_disk="$(md_parent_disk "$root_source")" || return 1
  device_disk="$(md_parent_disk "$device")" || return 1
  if [[ "$root_disk" == "$device_disk" ]]; then
    echo "ERROR: refusing to mount $label from root disk $root_disk" >&2
    return 1
  fi

  fstype="$(/usr/bin/sudo /sbin/blkid -s TYPE -o value -- "$device")"
  if [[ $? -ne 0 || -z "$fstype" ]]; then
    echo "ERROR: cannot determine filesystem type for $label ($device)" >&2
    return 1
  fi

  if [[ "$fstype" == "hfsplus" ]]; then
    mount_opts=(-o force,rw)
  elif [[ "$fstype" == "exfat" ]]; then
    /usr/bin/sudo /usr/sbin/modprobe fuse || return 1
  fi

  /usr/bin/sudo /usr/bin/install -d -m 0777 -o pi -g pi -- "$pth" || return 1
  mounted_target="$(/usr/bin/findmnt -rn -T "$pth" -o TARGET 2>/dev/null)"
  if [[ "$mounted_target" == "$pth" ]]; then
    echo "ERROR: $pth became a mount point before mounting $device; refusing" >&2
    return 1
  fi

  # Close the discovery-to-mount race by checking the selected exact label again.
  actual_label="$(/usr/bin/sudo /sbin/blkid -s "$label_key" -o value -- "$device")"
  if [[ $? -ne 0 || "$actual_label" != "$label" || ! -b "$device" ]]; then
    echo "ERROR: $device no longer has exact $label_key '$label'; refusing" >&2
    return 1
  fi

  echo "mounting exact $label_key '$label' from $device at $pth"
  /usr/bin/sudo /usr/bin/mount -t "$fstype" "${mount_opts[@]}" -- "$device" "$pth" || return 1

  mounted_target="$(/usr/bin/findmnt -rn -S "$device" -o TARGET)"
  if [[ "$mounted_target" != "$pth" ]]; then
    echo "ERROR: mount command succeeded but $device is not mounted at $pth" >&2
    return 1
  fi
  echo "mounted $label at $pth"
}

rm_mnt_dir() { # prevent Time Machine from backing up onto SD card etc
  local diskdir="$1"
  local resolve_status

  # fail closed: this deleted the mounted TM disk on 2026-07-14 when bare
  # `blkid` wasn't in pi's SSH PATH → empty output looked like "disk absent"
  if /usr/bin/grep -q " /mnt/$diskdir " /proc/mounts; then
    echo "NOT removing mount dir /mnt/$diskdir: currently mounted"
    return 0
  fi

  md_resolve_label "$diskdir"
  resolve_status=$?
  if (( resolve_status == 0 )); then
    echo "NOT removing mount dir /mnt/$diskdir for attached disk $MD_DEVICE"
    return 0
  elif (( resolve_status != 1 )); then
    echo "ERROR: disk discovery failed, refusing to remove /mnt/$diskdir"
    return 1
  fi

  [[ -d "/mnt/$diskdir" ]] || return 0
  echo "removing empty mount dir because no exact label is attached: /mnt/$diskdir"
  # rmdir only: a decoy dir is always empty; anything non-empty is real data
  /usr/bin/rmdir -- "/mnt/$diskdir" \
    || echo "ERROR: /mnt/$diskdir has contents, refusing to delete"
}

if [[ "$#" = "1" ]]; then
  mntdsk "$1"
elif [[ "$#" = "0" ]]; then # used by minutely cron job
  rm_dirs=(mbp1tbkup mbp2tbkup)
  for dir in "${rm_dirs[@]}"; do
    rm_mnt_dir "$dir"
  done

  disks=(movingparts mbp1tbkup mbp2tbkup hfs2tb usbext EXFAT512)

  for disk in "${disks[@]}"; do
    mntdsk "$disk"
  done
  # mntdsk mbbackup
  # mntdsk bigboi

fi


. /home/pi/scripts/fix_hfs_fs.sh 

echo "mounted disks:"
/usr/bin/grep "dev/sd" /proc/mounts

# 
# 
# If the optional argument new-label is present, then e2label will set the filesystem label to be new-label. 
# Ext2 filesystem labels can be at most 16 characters long; if new-label is longer than 16 characters, 
# e2label will truncate it and print a warning message. To set a new label, enter:
# # e2label /dev/sdb2 usbstroage
# 
# It is also possible to set the filesystem label using the -L option of tune2fs, enter:
# # tune2fs -L usbstroage /dev/sdb2
