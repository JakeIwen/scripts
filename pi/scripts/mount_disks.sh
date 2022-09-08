#! /bin/bash
# mntdsk sd_card 0383-ABDF


fetch_uuid() { /home/pi/scripts/fetch_disk_uuid.sh $1; }
fsprop() { 
  prop=$1 #  LABEL UUID TYPE PARTUUID PARTLABEL
  sterm=$2
  match="$(/sbin/blkid | grep "$sterm" | grep -Po "(?<=$prop=\")[^\"]*")"
  if [ -n "$sterm" ] && [ "$(echo "$match" | wc -l)" -gt 1 ]; then
    echo "multiple matches tosearch"
  fi
  echo "$match"
}

mntdsk() {
  label=$1
  pth="/mnt/$label"
  uuid=$(fetch_uuid $label)
  
  sudo mkdir -p $pth
  sudo chown pi $pth
  sudo chmod 777 $pth 
  # /sbin/blkid | grep mmcblk0p | grep $uuid && echo "not mounting $label because it is mmcfs" && return 1
  if [ -n "$uuid" ]; then
    fsroot=$(df -h | grep /boot | perl -pe 's|\d.*||g')
    /sbin/blkid | grep -P "$fsroot.*${uuid}" && echo "not mounting $label because it is rootFS: $fsroot" && return 1
    fstype=$(fsprop TYPE $uuid)
    
    sudo mount -U $uuid -t $fstype $opts $pth
  else
    fstype=$(fsprop TYPE $label)
    if [[ "$fstype" == "hfsplus" ]]; then opts="-o force,rw"; else opts=""; fi
    sudo mount PARTLABEL=$label -t $fstype $opts $pth
  fi
  

  # echo "mounted $label at $pth"
}

if [[ "$#" = "1" ]]; then
  mntdsk "$1"
else 45h7
  # mntdsk mbbackup
  mntdsk movingparts
  mntdsk hfs1tb
  mntdsk hfs2tb
  mntdsk bigboi
  mntdsk usbext

  mntdsk msd_nand2_boot && mntdsk msd_nand2 && mntdsk msd_nand2_settings
  mntdsk msd_nand1_boot && mntdsk msd_nand1 && mntdsk msd_nand1_settings
fi

echo "mounted disks:"
grep "dev/sd" /proc/mounts

# 
# 
# If the optional argument new-label is present, then e2label will set the filesystem label to be new-label. 
# Ext2 filesystem labels can be at most 16 characters long; if new-label is longer than 16 characters, 
# e2label will truncate it and print a warning message. To set a new label, enter:
# # e2label /dev/sdb2 usbstroage
# 
# It is also possible to set the filesystem label using the -L option of tune2fs, enter:
# # tune2fs -L usbstroage /dev/sdb2

