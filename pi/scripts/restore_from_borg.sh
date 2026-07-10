#!/bin/bash
# disaster restore: overlay a borg archive onto a freshly-flashed RPi OS card.
# for when the live SD *and* the hot spare are both gone — otherwise just swap the spare.
#   usage: restore_from_borg.sh <sdX> [archive]     (archive defaults to latest)
# target card must already be flashed with stock Raspberry Pi OS 64-bit
# (that provides the partition table + bootloader; we replace the contents).
set -eu
. /home/pi/scripts/backup/backup_conf.sh

disk=${1:?usage: restore_from_borg.sh <sdX> [archive]}
[[ "$disk" == mmcblk0* ]] && { echo "refusing to restore onto the live boot card"; exit 1; }
grep -q "^/dev/$disk" /proc/mounts && { echo "/dev/$disk has mounted partitions, unmount first"; exit 1; }
size_gb=$(( $(lsblk -bdno SIZE "/dev/$disk") / 1024**3 ))
[ "$size_gb" -le "$CLONE_MAX_DISK_GB" ] || { echo "refusing /dev/$disk (${size_gb}GB > ${CLONE_MAX_DISK_GB}GB guard)"; exit 1; }
if [ -f "$IGNITION_FLAG" ]; then
  echo "van is running — drives are unmount-protected; park first"; exit 1
fi
lock_disk "$disk"
acquire_job_lock || { echo "a backup/restore is already running, try again later"; exit 1; }

archive=${2:-$(borg list --last 1 --format '{archive}')}
boot_p="/dev/${disk}1"; root_p="/dev/${disk}2"
blkid "$boot_p" | grep -qi vfat || { echo "$boot_p is not a vfat boot partition — flash stock RPi OS first"; exit 1; }

mnt=/mnt/restore_root

# abort_backup.sh TERMs us if the van starts mid-restore: stop borg, unmount, leave a loud trail
child=
run() { "$@" & child=$!; wait "$child"; local rc=$?; child=; return $rc; }
aborted() {
  [ -n "$child" ] && { kill -TERM "$child" 2>/dev/null; wait "$child" 2>/dev/null; }
  cd /; umount "$mnt/boot/firmware" "$mnt" 2>/dev/null || true
  echo "RESTORE ABORTED — /dev/$disk is INCOMPLETE; re-run when parked"
  /home/pi/scripts/ntfy_send.sh "vanpi restore ABORTED" \
    "/dev/$disk is incomplete (van started mid-restore?) — re-run restore_from_borg.sh when parked" high rotating_light
  exit 143
}
trap aborted TERM INT

mkdir -p "$mnt"
mount "$root_p" "$mnt"
mkdir -p "$mnt/boot/firmware"
mount "$boot_p" "$mnt/boot/firmware"

echo "extracting $archive over $mnt (replacing the stock rootfs)"
find "$mnt" -mindepth 1 -maxdepth 1 ! -name boot -exec rm -rf {} +
rm -rf "$mnt/boot/firmware/"*
cd "$mnt"
run borg extract --numeric-ids "::$archive" || [ $? -eq 1 ]  # rc 1 = warnings (vfat can't hold ownership), fine

# point the restored system at ITS OWN partitions — the step the old scheme missed for fstab
diskid=$(blkid -o value -s PTUUID "/dev/$disk")
sed -i -E "s/PARTUUID=[0-9a-f]{8}-0([12])/PARTUUID=$diskid-0\1/g" \
  "$mnt/etc/fstab" "$mnt/boot/firmware/cmdline.txt"

touch "$mnt/forcefsck"
cd /
umount "$mnt/boot/firmware" "$mnt"
echo "done — /dev/$disk should boot as $(hostname) at state: $archive"
