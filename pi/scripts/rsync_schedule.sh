#! /bin/bash
date;

SEEGAYTE_MOUNTED=$(mount | awk '/seegayte/ {print $6}' | grep "rw")
MP_MOUNTED=$(mount | awk '/movingparts/ {print $6}' | grep "rw")

[ ! $SEEGAYTE_MOUNTED ] && echo "Seegayte not available/writable" && exit 0
sudo rsync -avH --delete-during --delete-excluded --exclude-from=/rsync-exclude.txt / /mnt/seegayte/pi_backup/

[ ! $MP_MOUNTED ] && echo "MP not available/writable" && exit 0
sudo rsync -avH --delete-during --delete-excluded /mnt/movingparts /mnt/seegayte/mp_backup/;
sudo rsync -avH --delete-during --delete-excluded /mnt/movingparts /mnt/seegayte/mp_backup/;
