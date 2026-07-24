#!/bin/bash
# Manual full disk remount.  umount_disks.sh owns exact consumer shutdown:
# it stops qbittorrent-nox only when movingparts is involved and closes only
# the Samba shares backed by disks being unmounted.
set -u

. /home/pi/scripts/umount_disks.sh || exit 1
. /home/pi/scripts/mount_disks.sh || exit 1
. /home/pi/scripts/fix_hfs_fs.sh || exit 1
sudo /usr/sbin/service smbd start
