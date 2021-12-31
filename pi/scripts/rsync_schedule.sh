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

# mntdsk vfat /mnt/sd_card 0383-ADFD
mntdsk() {
  fstype=$1
  dirname=$2
  uuid=$3
  pth="/mnt/$dirname"
  
  sudo mkdir -p $pth && sudo chown pi $pth  && sudo chmod 777 $pth 
  sudo mount -U "$uuid" -t $fstype -o nofail $pth && echo "mounted $dirname at $pth"
}

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
