#! /bin/bash
# args destination ie /dev/sda

s() { name=$1; shift; /home/pi/scripts/$name.sh "$@"; }

fsprop() { 
  prop=$1 #  LABEL UUID TYPE PARTUUID PARTLABEL
  sterm=$2
  match="$(blkln_name "$sterm" | grep -Po "(?<= $prop=\")[^\"]*")"
  if [ -n "$sterm" ] && [ "$(echo "$match" | wc -l)" -gt 1 ]; then
    echo "multiple matches tosearch"
    echo "prop $prop"
    echo "sterm $sterm"
    echo "match $match"
  fi
  echo "$match"
}

blkln_name() { 
  sterm=$1
  match="$(/sbin/blkid | grep -P "^$sterm")"
  echo "$match"
}

rsync_flags="--recursive --delete-during --delete-excluded --exclude-from=/home/pi/rsync-exclude.txt"

s mount_disks bigboi
source_file=/mnt/bigboi/pi_backup_git/dd_mmcblk0p1
dev_sdx=$1

if [ $(fsprop LABEL_FATBOOT ${dev_sdx}1) != 'bootfs' ]; then
  echo "incorrectly formatted drive." 
  echo "please flash sd card toworking stock RPi x64 first"
  exit || return
fi
if [ -e "$source_file" ] && [ -e $dev_sdx ]; then
  echo "mounting source file: $source_file"
  sudo mount "$source_file" /mnt/sd_backup/
  echo "mounting destination: ${dev_sdx}1"
  sudo mount ${dev_sdx}1 /mnt/sd_clone/
  echo "beginning rsync"
  sudo rm -rf /mnt/sd_clone/*
  sudo rsync -aHv --recursive /mnt/sd_backup/* /mnt/sd_clone
  
  root_uuid="$(fsprop PARTUUID ${dev_sdx}2)"
  echo "setting root_uuid to $root_uuid"
  sudo sed -E -i "s|\w{8}-\w{2}|$root_uuid|g" /mnt/sd_clone/cmdline.txt
  sudo umount /mnt/sd_backup 
  sudo umount /mnt/sd_clone 
  
else
  echo "missing files"
fi

