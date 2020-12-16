#! /bin/bash
 
 MP_MOUNTED=$(mount | awk '/movingparts/ {print $6}' | grep "rw")
 echo "MP_MOUNTED: $MP_MOUNTED";
 if [ $MP_MOUNTED ]
 then rsync -avH --delete-during --delete-excluded --exclude-from=/rsync-exclude.txt / /mnt/movingparts/pi_backup/;
 else echo "Movingparts drive not available or not writable";
 fi