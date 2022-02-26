#! /bin/bash
# cron-scheduled daily

chosen_msd=/mnt/msd_nand2
sd_boot_path=${chosen_msd}_boot
sd_root_path=$chosen_msd
hdd_backup=/mnt/bigboi/pi_backup_git/pi_backup

echo "start `date`, for $chosen_msd";
rsync_flags="--delete-during --delete-excluded --exclude-from=/rsync-exclude-media.txt"
MP_MOUNTED=$(mount | awk '/movingparts/ {print $6}' | grep "rw")
BIGBOI_MOUNTED=$(mount | awk '/bigboi/ {print $6}' | grep "rw")
MSD1_MOUNTED=$(mount | awk '/msd_nand1/ {print $6}' | grep "rw")
MSD2_MOUNTED=$(mount | awk '/msd_nand2/ {print $6}' | grep "rw")


[ ! $BIGBOI_MOUNTED ] && echo "bigboi not available/writable" && exit 0
 
if [ $MP_MOUNTED ]; then
  sudo rsync -aH $rsync_flags /mnt/movingparts/ /mnt/bigboi/mp_backup
  . /home/pi/scripts/alias_media.sh
else echo "MP 2TB not available/writable"
fi

# pi backups
sync_pi_backup() {
  rsync_flags="-avH --delete-during --delete-excluded"
  # store in git repo on HDD drive
  echo "beginning pibackup to hdd `date`"
  sudo rsync -aH $rsync_flags / $hdd_backup
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
  chosen_msd=/mnt/msd_nand2
  sd_boot_path=${chosen_msd}_boot
  sd_root_path=$chosen_msd
  hdd_backup=/mnt/bigboi/pi_backup_git/pi_backup
  # echo "resetting git"
  echo "beginning restore to microSD `date`"
  # git reset > /dev/null
  sudo rm -rf $sd_boot_path/*
  sudo rm -rf $sd_root_path/*
  echo "sdcard prepped, starting boot copy"
  sudo cp -a $hdd_backup/boot/* $sd_boot_path/
  sudo rm -rf $hdd_backup/boot
  echo "boot copied, beginng root"
  sudo cp -a $hdd_backup/* $sd_root_path/  
  echo "done with microSD `date`"
}

sync_pi_backup
commit_last_backup      
[[ $MSD1_MOUNTED ]] || [[ $MSD2_MOUNTED ]] && retore_to_msd

# sudo rsync -avH -e 'ssh -i /home/pi/.ssh/id_rsa' $rsync_flags / root@192.168.6.1:/mnt/sda1


echo "done `date`";
