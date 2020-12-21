#! /bin/bash
SEEGAYTE_MOUNTED=$(mount | awk '/seegayte/ {print $6}' | grep "rw")
  if [ $SEEGAYTE_MOUNTED ]; then
    echo "SEEGAYTE_MOUNTED: $SEEGAYTE_MOUNTED";
    date;
    # rsync -avH --delete-during --delete-excluded --exclude-from=/rsync-exclude.txt / /mnt/seegayte/pi_backup/;
    MP_MOUNTED=$(mount | awk '/movingparts/ {print $6}' | grep "rw")
    echo "MP_MOUNTED: $MP_MOUNTED";
    if [ $MP_MOUNTED ]
    then sudo rsync -avH --delete-during --delete-excluded /mnt/movingparts /mnt/seegayte/mp_backup/;
    else echo "Movingparts drive not available or not writable";
    fi
 else echo "Seegayte drive not available or not writable";
 fi