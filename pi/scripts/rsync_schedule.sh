#! /bin/bash
# cron-scheduled daily
echo "start `date`";
rsync_flags="-avH --delete-during --delete-excluded"
MP_MOUNTED=$(mount | awk '/movingparts/ {print $6}' | grep "rw")
BIGBOI_MOUNTED=$(mount | awk '/bigboi/ {print $6}' | grep "rw")
MICROSD_MOUNTED=$(mount | awk '/micro_sd/ {print $6}' | grep "rw")

[ ! $BIGBOI_MOUNTED ] && echo "bigboi not available/writable" && exit 0

if [ $MP_MOUNTED ]; then
  sudo rsync $rsync_flags --exclude-from=/rsync-exclude-media.txt /mnt/movingparts/ /mnt/bigboi/mp_backup
  . /home/pi/scripts/alias_media.sh
else echo "MP 2TB not available/writable"
fi

# pi backups
sync_pi_backup() {
  rsync_flags="-avH --delete-during --delete-excluded"
  hdd_backup=/mnt/bigboi/pi_backup_git/pi_backup
  sd_backup=/mnt/micro_sd/
  sd_live=/mnt/micro_sd/_backup_live
  sd_old=/mnt/micro_sd/_backup_old
  
  # store in git repo on HDD drive
  echo "beginning pibackup to hdd `date`"
  sudo rsync $rsync_flags --exclude-from=/rsync-exclude.txt / $hdd_backup
  
  echo "beginning pibackup to microSD `date`"
  sudo rsync $rsync_flags --exclude-from=/rsync-exclude.txt / $sd_live
  
  echo "done with microSD `date`"
  
  files=`sudo ls -d ${sd_backup}* | grep -v '_backup_' | xargs -I {} echo {}`
  sudo mv $files $sd_old/
  sudo mv $sd_live/* $sd_backup/
  
}

[ $MICROSD_MOUNTED ] && sync_pi_backup

# sudo rsync -avH -e 'ssh -i /home/pi/.ssh/id_rsa' --delete-during --delete-excluded --exclude-from=/rsync-exclude.txt / root@192.168.6.1:/mnt/sda1

cd /mnt/bigboi/pi_backup_git || exit 
sudo git add .
msg="Pi Backup - $(date)"
sudo git commit -m "$msg"
commit=`git rev-parse HEAD`
echo "sudo git checkout $commit" > "./$msg.sh"

echo "done `date`";
