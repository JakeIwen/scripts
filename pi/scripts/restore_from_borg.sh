#!/bin/bash
# disaster restore: overlay a borg archive onto a freshly-flashed RPi OS card.
# for when the live SD *and* the hot spare are both gone — otherwise just swap the spare.
#   usage: restore_from_borg.sh <sdX> [archive]     (archive defaults to latest)
# target card must already be flashed with stock Raspberry Pi OS 64-bit
# (that provides the partition table + bootloader; we replace the contents).
set -eu
. /home/pi/scripts/backup_conf.sh

disk=${1:?usage: restore_from_borg.sh <sdX> [archive]}
[[ "$disk" == mmcblk0* ]] && { echo "refusing to restore onto the live boot card"; exit 1; }
grep -q "^/dev/$disk" /proc/mounts && { echo "/dev/$disk has mounted partitions, unmount first"; exit 1; }
size_gb=$(( $(lsblk -bdno SIZE "/dev/$disk") / 1024**3 ))
[ "$size_gb" -le "$CLONE_MAX_DISK_GB" ] || { echo "refusing /dev/$disk (${size_gb}GB > ${CLONE_MAX_DISK_GB}GB guard)"; exit 1; }

archive=${2:-$(borg list --last 1 --format '{archive}')}
boot_p="/dev/${disk}1"; root_p="/dev/${disk}2"
blkid "$boot_p" | grep -qi vfat || { echo "$boot_p is not a vfat boot partition — flash stock RPi OS first"; exit 1; }

mnt=/mnt/restore_root
mkdir -p "$mnt"
mount "$root_p" "$mnt"
mkdir -p "$mnt/boot/firmware"
mount "$boot_p" "$mnt/boot/firmware"

echo "extracting $archive over $mnt (replacing the stock rootfs)"
find "$mnt" -mindepth 1 -maxdepth 1 ! -name boot -exec rm -rf {} +
rm -rf "$mnt/boot/firmware/"*
cd "$mnt"
borg extract --numeric-ids "::$archive" || [ $? -eq 1 ]  # rc 1 = warnings (vfat can't hold ownership), fine

# point the restored system at ITS OWN partitions — the step the old scheme missed for fstab
diskid=$(blkid -o value -s PTUUID "/dev/$disk")
sed -i -E "s/PARTUUID=[0-9a-f]{8}-0([12])/PARTUUID=$diskid-0\1/g" \
  "$mnt/etc/fstab" "$mnt/boot/firmware/cmdline.txt"

touch "$mnt/forcefsck"
cd /
umount "$mnt/boot/firmware" "$mnt"
echo "done — /dev/$disk should boot as $(hostname) at state: $archive"
