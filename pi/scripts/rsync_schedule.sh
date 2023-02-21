#! /bin/bash
# cron-scheduled daily

hdd_backup=/mnt/bigboi/pi_backup_git/pi_backup
backup_msd=/mnt/msd_nand2

rsync_media_flags="--delete-during --delete-excluded --exclude-from=/rsync-exclude-media.txt"
z_flags="--delete-during --delete-excluded --exclude-from=/rsync-exclude.txt"
MP_MOUNTED=$(mount | awk '/movingparts/ {print $6}' | grep "rw")
# MSD1_MOUNTED=$(mount | grep -P '^/dev/sd' | awk '/msd_nand1/ {print $6}' | grep "rw")
# MSD2_MOUNTED=$(mount | grep -P '^/dev/sd' | awk '/msd_nand2/ {print $6}' | grep "rw")
BACKUP_MSD_MOUNTED=$(mount | grep -P '^/dev/sd' | grep "$backup_msd " | grep "rw")

s() { name=$1; shift; /home/pi/scripts/$name.sh "$@"; }

mount_bb() {
  s mount_disks bigboi
  sleep 2
  BIGBOI_MOUNTED=$(mount | awk '/bigboi/ {print $6}' | grep "rw")
  if [ ! $BIGBOI_MOUNTED ]; then
    s sms_send "bigboi not available/writable. EXITING"
    exit || return
  fi
}

unmount_bb() { s umount_disks bigboi; }

sync_mp_bb() {
  if [ $MP_MOUNTED ]; then
    sudo rsync -aH $rsync_media_flags /mnt/movingparts/ /mnt/bigboi/mp_backup
    s alias_media
  else 
    s sms_send "MP 2TB not available/writable"
  fi
}

# pi backups
# live_pi_backup() {
#   # store in git repo on HDD drive
#   echo "beginning pibackup to hdd `date`"
#   sudo rsync -aH $rsync_flags / $hdd_backup
#   echo "hdd complete `date`"
# }

live_pi_backup() {
    echo "beginning pibackup to hdd `date`"
  outfile="/mnt/bigboi/pi_backup_git/dd_mmcblk0"
  # Create trigger to force file system consistency check if image is restored
  touch /boot/forcefsck
  dd if=/dev/mmcblk0 of="$outfile" bs=1M
  # Remove fsck trigger
  rm /boot/forcefsck
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
  if [[ ! $backup_msd ]]; then
    s sms_send "no backup_msd specified"
    exit || return
  fi
  if [[ ! $BACKUP_MSD_MOUNTED ]]; then
    s sms_send "no BACKUP_MSD_MOUNTED"
    exit || return
  fi
  
  echo "beginning restore to microSD $backup_msd - `date`"
  if [[ ! -e "$backup_msd/boot" ]]; then
    echo "$backup_msd/boot not found for copying - resetting git"
    sudo git reset --quiet > /dev/null
    echo "git reset complete"
  fi
  
  sd_boot_path=${backup_msd}_boot
  # sd_settings_path=${backup_msd}_settings
  sd_root_path=$backup_msd
  
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
  if [ "$pctfull" -gt 70 ]; then
    s sms_send "main SD card unusually full ($pctfull)%, aborting"
    exit || return 
  fi
}

echo -e "\nscheduled_rsync begin: `date`"
mount_bb
sync_mp_bb
chk_free_sd_space
live_pi_backup
commit_last_backup      
# retore_to_msd
unmount_bb
echo "scheduled_rsync end: `date`";

# sudo rsync -avH -e 'ssh -i /home/pi/.ssh/id_rsa' $rsync_flags / root@192.168.6.1:/mnt/sda1
