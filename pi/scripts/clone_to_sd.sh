#!/bin/bash
# maintain a bootable hot-spare SD card with rpi-clone
#   clone_to_sd.sh <label>               incremental clone to the attached card whose rootfs label is <label>
#   clone_to_sd.sh --init <label> <sdX>  first-time setup of a fresh card (DESTROYS its contents)
# exit: 0 ok, 1 failed, 2 card not attached (quiet — watchdog handles staleness)
set -u
. /home/pi/scripts/backup/backup_conf.sh
notify() { /home/pi/scripts/ntfy_send.sh "$@"; }

init=0
[ "${1:-}" = "--init" ] && { init=1; shift; }
label=${1:?usage: clone_to_sd.sh [--init] <label> [sdX]}

if [ $init = 1 ]; then
  disk=${2:?--init needs the target device, e.g. sda}
else
  part=$(blkid -t "LABEL=$label" -o device 2>/dev/null | head -1)
  [ -n "$part" ] || { echo "no attached card labeled $label"; exit 2; }
  disk=$(lsblk -no pkname "$part")
fi

[ -b "/dev/$disk" ] || { echo "/dev/$disk is not a block device"; exit 1; }
[[ "$disk" == mmcblk0* ]] && { echo "refusing to clone onto the live boot card"; exit 1; }
lock_disk "$disk" || exit 1
size_gb=$(( $(lsblk -bdno SIZE "/dev/$disk") / 1024**3 ))
if [ "$size_gb" -gt "$CLONE_MAX_DISK_GB" ]; then
  notify "vanpi clone" "refusing /dev/$disk: ${size_gb}GB exceeds ${CLONE_MAX_DISK_GB}GB guard" high warning
  exit 1
fi
if grep -q "^/dev/$disk" /proc/mounts; then
  notify "vanpi clone" "/dev/$disk has mounted partitions, not cloning" high warning
  exit 1
fi

start=$(date +%s)
if [ $init = 1 ]; then
  echo "initializing /dev/$disk as $label — full clone, erases the card"
  rpi-clone "$disk" -f -U \
    || { notify "vanpi clone" "initial clone to $label (/dev/$disk) failed" high rotating_light; exit 1; }
else
  rpi-clone "$disk" -U \
    || { notify "vanpi clone" "clone to $label (/dev/$disk) failed" high rotating_light; exit 1; }
fi
# the label is how future runs find this card (device names re-enumerate constantly here)
e2label "/dev/${disk}2" "$label"
took=$(( $(date +%s) - start ))

# stamp the card itself so a card in hand tells you what's on it
mkdir -p /mnt/clone_boot
if mount "/dev/${disk}1" /mnt/clone_boot 2>/dev/null; then
  printf 'cloned from %s on %s (rpi-clone, %ss)\n' "$(hostname)" "$(date '+%F %T')" "$took" \
    > /mnt/clone_boot/CLONE_INFO.txt
  umount /mnt/clone_boot
fi

mkdir -p "$STAMP_DIR"
date '+%F %T' > "$STAMP_DIR/clone_$label"
notify "vanpi clone OK" \
  "$label is bootable + current as of $(date '+%F %H:%M') (took $((took/60))m$((took%60))s)" \
  default floppy_disk

[ $init = 1 ] && echo "NOTE: add '$label:<interval-days>' to CLONE_TARGETS in backup_conf.sh to schedule it"
exit 0
