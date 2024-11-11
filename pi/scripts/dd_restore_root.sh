#! /bin/bash

if [ -n "$(sudo lsblk -o NAME,SIZE | grep $1 | grep T)" ]; then
  echo "you just tried to restore an RasPi OS to a Terabyte-sized drive, you fucking idiot"
  echo "you chose $1 in:"
  sudo lsblk -o NAME,FSTYPE,SIZE,MOUNTPOINT,LABEL
  exit || return
fi
dev_sdx=/dev/$1 # /dev/sdi etc
# ensure source file is the desired commit
infile=/mnt/bigboi/pi_backup_git.x64/dd_mmcblk0
infile1="${infile}p1"
infile2="${infile}p2"
s() { name=$1; shift; /home/pi/scripts/$name.sh "$@"; }
s mount_disks bigboi

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

if [ -e "$infile1" ] && [ -e "$infile2" ] && [ -e $dev_sdx ]; then
  # echo "beginning restore of $infile1 & $infile2 to $dev_sdx"
  # sudo dd if="$infile1" of="${dev_sdx}1" bs=4M status=progress conv=fsync
  # set_rand_vfat_uuid "${dev_sdx}1"
  # echo "done with $infile1, new UUID set"
  # 
  sudo dd if="$infile2" of="${dev_sdx}2" bs=4M status=progress conv=fsync
  echo "done with $infile2, setting new UIID"
  sudo e2fsck -f "${dev_sdx}2" -y
  sudo tune2fs -U "$(uuidgen)" "${dev_sdx}2"
  echo "uuid set"
  
  # root_uuid="$(fsprop PARTUUID ${dev_sdx}2)"
  # sudo mount ${dev_sdx}1 /mnt/tmp 
  # sudo sed -E -i "s|\w{8}-\w{2}|$root_uuid|g" /mnt/tmp/cmdline.txt
  # sudo umount /mnt/tmp
  
  
elif [ -e "$infile" ] && [ -e $dev_sdx ]; then
  echo "beginning restore of $infile to $dev_sdx"
  sudo dd if="$infile" of="$dev_sdx" bs=4M status=progress conv=fsync
else
  echo "couldnt find all files to copy to $dev_sdx"
fi

