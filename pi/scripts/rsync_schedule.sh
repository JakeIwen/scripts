#! /bin/bash
# cron-scheduled daily


set_vfat_uuid() {
  UUID=$1 # 1234-ABCF  hex only
  BLKID=$2 # /dev/sdc1
  valid=`echo "$UUID" | grep -P "^\d{4}\-[A-F]{4}$"`
  vfat=`blkid $BLKID | grep 'TYPE="vfat"'`
  
  echo "Current UUID:"
  sudo dd bs=1 skip=67 count=4 if=$BLKID 2>/dev/null \
    | xxd -plain -u \
    | sed -r 's/(..)(..)(..)(..)/\4\3-\2\1/' 
    
  if [ -n "$valid" ] && [ -n "$vfat" ]; then
    printf "\x${UUID:7:2}\x${UUID:5:2}\x${UUID:2:2}\x${UUID:0:2}" \
      | sudo dd bs=1 seek=67 count=4 conv=notrunc of=$BLKID
    
    echo "Updated UUID:"
    sudo dd bs=1 skip=67 count=4 if=$BLKID 2>/dev/null \
      | xxd -plain -u \
      | sed -r 's/(..)(..)(..)(..)/\4\3-\2\1/' 
      
  else
    echo "UUID does not match '1234-ABCD' form"
    echo "or '`blkid $BLKID | grep -o 'TYPE="[^\"]*"'`' is not a vfat partition"
  fi
}



chosen_msd=/mnt/msd_nand1
sd_boot=${chosen_msd}_boot
sd_root=$chosen_msd

echo "start `date`";
rsync_flags="-avH --delete-during --delete-excluded"
MP_MOUNTED=$(mount | awk '/movingparts/ {print $6}' | grep "rw")
BIGBOI_MOUNTED=$(mount | awk '/bigboi/ {print $6}' | grep "rw")
MSD1_MOUNTED=$(mount | awk '/msd_nand1/ {print $6}' | grep "rw")
MSD2_MOUNTED=$(mount | awk '/msd_nand2/ {print $6}' | grep "rw")
hdd_backup=/mnt/bigboi/pi_backup_git/pi_backup


[ ! $BIGBOI_MOUNTED ] && echo "bigboi not available/writable" && exit 0
 
if [ $MP_MOUNTED ]; then
  sudo rsync $rsync_flags --exclude-from=/rsync-exclude-media.txt /mnt/movingparts/ /mnt/bigboi/mp_backup
  . /home/pi/scripts/alias_media.sh
else echo "MP 2TB not available/writable"
fi

# pi backups
sync_pi_backup() {
  rsync_flags="-avH --delete-during --delete-excluded"
  # store in git repo on HDD drive
  echo "beginning pibackup to hdd `date`"
  sudo rsync $rsync_flags --exclude-from=/rsync-exclude.txt / $hdd_backup
  echo "hdd complete`date`"
}

commit_last_backup() {
  cd /mnt/bigboi/pi_backup_git || exit 
  sudo git add .
  msg=$(echo "pibackup $(date)" | sed 's| |_|g')
  sudo git commit -m "$msg"
  commit=`git rev-parse HEAD`
  echo "sudo git checkout $commit" > "./$msg.sh"
}

retore_to_msd() {
  echo "resetting git"
  git reset
  echo "beginning restore to microSD `date`"
  sudo mkdir -p $sd_root/_backup
  sudo mv $sd_root/* $sd_root/_backup 
  echo "moved sd root"
  sudo mkdir -p $sd_boot/_backup_boot/
  echo "moved sd boot"
  sudo cp -r --preserve $hdd_backup/boot/* $sd_root/_backup_boot/ 
  sudo rm -rf $hdd_backup/boot
  echo "boot copied, beginng root"
  # sudo mv $hdd_backup/* $sd_root/*
  sudo cp -r $hdd_backup/* $sd_root/  
  echo "done with microSD `date`"
  echo "resetting git `date`"
  git reset
  echo "git reset `date`"
}
# 

sync_pi_backup
commit_last_backup
[[ $MSD1_MOUNTED ]] && retore_to_msd

sudo rsync -avH -e 'ssh -i /home/pi/.ssh/id_rsa' --delete-during --delete-excluded --exclude-from=/rsync-exclude.txt / root@192.168.6.1:/mnt/sda1


echo "done `date`";
