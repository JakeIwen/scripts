#! /bin/bash

BACKUP_MOUNTED=$(mount | awk '/movingparts/ {print $6}' | grep "rw")
  if [ $BACKUP_MOUNTED ]; then
    echo "Commencing Backup: $BACKUP_MOUNTED";
    date;
    rsync -avH --delete-during --delete-excluded --exclude-from=/rsync-exclude.txt / /mnt/seegayte/pi_backup/;
    rsync -avH --delete-during --delete-excluded /mnt/movingparts /mnt/seegayte/mp_backup/;
 else echo "Backup drive not available or not writable";
 fi