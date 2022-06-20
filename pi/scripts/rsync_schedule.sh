#! /bin/bash
# cron-scheduled daily

hdd_backup=/mnt/bigboi/pi_backup_git/pi_backup
backup_msd=/mnt/msd_nand2

rsync_media_flags="--delete-during --delete-excluded --exclude-from=/rsync-exclude-media.txt"
rsync_flags="--delete-during --delete-excluded --exclude-from=/rsync-exclude.txt"
MP_MOUNTED=$(mount | awk '/movingparts/ {print $6}' | grep "rw")
# MSD1_MOUNTED=$(mount | grep -P '^/dev/sd' | awk '/msd_nand1/ {print $6}' | grep "rw")
# MSD2_MOUNTED=$(mount | grep -P '^/dev/sd' | awk '/msd_nand2/ {print $6}' | grep "rw")
BACKUP_MSD_MOUNTED=$(mount | grep -P '^/dev/sd' | grep "$backup_msd " | grep "rw")

mount_bb() {
  . /home/pi/scripts/mount_disks.sh bigboi
  sleep 2
  BIGBOI_MOUNTED=$(mount | awk '/bigboi/ {print $6}' | grep "rw")
  [ ! $BIGBOI_MOUNTED ] && echo "bigboi not available/writable. EXITING" && exit 0
}

unmount_bb() { . /home/pi/scripts/umount_disks.sh bigboi; }

sync_mp_bb() {
  if [ $MP_MOUNTED ]; then
    sudo rsync -aH $rsync_media_flags /mnt/movingparts/ /mnt/bigboi/mp_backup
    . /home/pi/scripts/alias_media.sh
  else 
    echo "MP 2TB not available/writable"
  fi
}

# pi backups
live_pi_backup() {
  # store in git repo on HDD drive
  echo "beginning pibackup to hdd `date`"
  sudo rsync -avH $rsync_flags / $hdd_backup
  echo "hdd complete `date`"
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
  [[ ! $backup_msd ]] && exit || return
  sd_boot_path=${backup_msd}_boot
  # sd_settings_path=${backup_msd}_settings
  sd_root_path=$backup_msd
  
  echo "beginning restore to microSD $backup_msd - `date`"
  if [[ ! -e "$backup_msd/boot" ]]; then
    echo "$backup_msd/boot not found for copying - resetting git"
    sudo git reset --quiet > /dev/null
    echo "git reset complete"
  fi
  
  sudo rm -rf $sd_boot_path/* # lets make sure these variables are defined lol
  sudo rm -rf $sd_root_path/* 
  
  echo "sdcard prepped, starting boot copy"
  sudo cp -a $hdd_backup/boot/* $sd_boot_path/
  sudo chmod --reference="$hdd_backup/boot" $sd_boot_path
  sudo rm -rf $hdd_backup/boot
  echo "boot copied, beginng root"
  sudo cp -a $hdd_backup/* $sd_root_path/  
  echo "done with microSD `date`"
}

chk_free_sd_space() {
  pctfull=$(df -h | grep /dev/root | grep -Po '\d+%' | grep -Po '\d+')
  if [ "$pctfull" -gt 55 ]; then
    echo "main SD card unusually full ($pctfull)%, aborting"
    exit || return 
  fi
}

echo -e "\nscheduled_rsync begin: `date`"
mount_bb
sync_mp_bb
chk_free_sd_space
live_pi_backup
commit_last_backup      
[[ $BACKUP_MSD_MOUNTED ]] && retore_to_msd
unmount_bb
# sudo rsync -avH -e 'ssh -i /home/pi/.ssh/id_rsa' $rsync_flags / root@192.168.6.1:/mnt/sda1

echo "scheduled_rsync end: `date`";
