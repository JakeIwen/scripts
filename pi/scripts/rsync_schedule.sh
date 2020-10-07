#! /bin/bash

BACKUP_MOUNTED=$(mount | awk '/movingparts/ {print $6}' | grep "rw")
  if [ $BACKUP_MOUNTED ]; then
    echo $BACKUP_MOUNTED;
    echo "Commencing Backup";
   rsync -avH --delete-during --delete-excluded --exclude-from=/rsync-exclude.txt / /mnt/movingparts/pi_backup/;
 else echo "Backup drive not available or not writable";
 fi