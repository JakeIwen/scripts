#! /bin/bash

fetch_uuid() { /home/pi/scripts/fetch_disk_uuid.sh $1; }

# mntdsk sd_card 0383-ABDF
mntdsk() {
  fname=$1
  pth="/mnt/$fname"
  uuid=$(fetch_uuid $fname)
  fstype=`blkid | grep $uuid | grep -Po '(?<=TYPE=")[^"]*'`

  if [[ "$fstype" == "hfsplus" ]]; then opts="-o force,rw"; else opts=""; fi
    
  sudo mkdir -p $pth && sudo chown pi $pth  && sudo chmod 777 $pth 
  sudo mount -U $uuid -t $fstype $opts $pth && echo "mounted $fname at $pth"
}

mntdsk mbbackup
mntdsk movingparts
mntdsk bigboi
mntdsk seegayte

mntdsk msd_nand2
mntdsk msd_nand2_boot
mntdsk msd_nand2_settings

mntdsk msd_nand1
mntdsk msd_nand1_boot
mntdsk msd_nand1_settings
# 
# 
# If the optional argument new-label is present, then e2label will set the filesystem label to be new-label. 
# Ext2 filesystem labels can be at most 16 characters long; if new-label is longer than 16 characters, 
# e2label will truncate it and print a warning message. To set a new label, enter:
# # e2label /dev/sdb2 usbstroage
# 
# It is also possible to set the filesystem label using the -L option of tune2fs, enter:
# # tune2fs -L usbstroage /dev/sdb2
