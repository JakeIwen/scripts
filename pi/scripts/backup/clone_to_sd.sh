#!/bin/bash
# maintain a bootable hot-spare SD card with rpi-clone
#   /home/pi/scripts/backup/clone_to_sd.sh <label>               incremental clone to the attached card whose rootfs label is <label>
#   /home/pi/scripts/backup/clone_to_sd.sh --init <label> <sdX>  first-time setup of a fresh card (DESTROYS its contents)
# exit: 0 ok, 1 failed, 2 card not attached (quiet — watchdog handles staleness)
set -u
. /home/pi/scripts/backup/backup_conf.sh
backup_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
. "$backup_script_dir/../disk_policy.sh" || exit 1
notify() { /home/pi/scripts/ntfy_send.sh "$@"; }

init=0
[ "${1:-}" = "--init" ] && { init=1; shift; }
label=${1:?usage: /home/pi/scripts/backup/clone_to_sd.sh [--init] <label> [sdX]}

if [ $init = 1 ]; then
  disk=${2:?--init needs the target device, e.g. sda}
else
  disk_policy_resolve_exact_label "$label" label-only
  resolve_status=$?
  if (( resolve_status == 1 )); then
    echo "no attached card labeled $label"
    exit 2
  elif (( resolve_status != 0 )); then
    echo "unsafe label mapping for $label: $DISK_POLICY_RESOLVE_ERROR" >&2
    exit 1
  fi
  part=$DISK_POLICY_RESOLVED_DEVICE
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
# background I/O priority: a slow or dying card must not starve the rest of the
# system (2026-07-14: a failing card in D-state took down ssh for the whole pi)
throttle="ionice -c2 -n7 nice -n10"
clone_log="/tmp/vanpi_clone_${disk}_$$.log"
cleanup_clone_log() { [ ! -e "$clone_log" ] || unlink "$clone_log"; }
trap cleanup_clone_log EXIT
if [ $init = 1 ]; then
  echo "initializing /dev/$disk as $label — full clone, erases the card"
  $throttle rpi-clone "$disk" -f -U 2>&1 | tee "$clone_log"
else
  $throttle rpi-clone "$disk" -U 2>&1 | tee "$clone_log"
fi
# rpi-clone 2.0.27 can print an rsync code 23 error but still exit zero. Treat
# either signal as failure so a partial clone is never stamped current.
clone_rc=${PIPESTATUS[0]}
if [ "$clone_rc" -ne 0 ] || grep -q '^rsync error:' "$clone_log"; then
  notify "vanpi clone" \
    "clone to $label (/dev/$disk) was incomplete (rpi-clone=$clone_rc or rsync error)" \
    high rotating_light
  exit 1
fi
# rpi-clone has unmounted both target partitions. A forced, read-only check
# catches corrupt destination metadata before the card is advertised as a
# bootable recovery generation.
if ! e2fsck -fn "/dev/${disk}2"; then
  notify "vanpi clone" \
    "clone to $label (/dev/$disk) failed post-clone filesystem verification" \
    high rotating_light
  exit 1
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
