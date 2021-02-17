#! /bin/bash
date;

MP_MOUNTED=$(mount | awk '/movingparts/ {print $6}' | grep "rw")
BIGBOI_MOUNTED=$(mount | awk '/bigboi/ {print $6}' | grep "rw")

[ ! $BIGBOI_MOUNTED ] && echo "bigboi not available/writable" && exit 0
sudo rsync -avH --delete-during --delete-excluded --exclude-from=/rsync-exclude.txt / /mnt/bigboi/pi_backup_git/pi_backup

cd /mnt/bigboi/pi_backup_git  || exit
sudo git add .
msg="Pi Backup - $(date)"
sudo git commit -m "$msg"


[ ! $MP_MOUNTED ] && echo "MP 2TB not available/writable" && exit 0
sudo rsync -avH --delete-during --delete-excluded /mnt/movingparts /mnt/bigboi/mp_backup;
. /home/pi/scripts/alias_media.sh

