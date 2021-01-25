#! /bin/bash
date;

SEEGAYTE_MOUNTED=$(mount | awk '/seegayte/ {print $6}' | grep "rw")
# MP_MOUNTED=$(mount | awk '/movingparts/ {print $6}' | grep "rw")
BIGBOI_MOUNTED=$(mount | awk '/bigboi/ {print $6}' | grep "rw")

[ ! $BIGBOI_MOUNTED ] && echo "bigboi not available/writable" && exit 0
sudo rsync -avH --delete-during --delete-excluded --exclude-from=/rsync-exclude.txt / /mnt/bigboi/pi_backup/

# [ ! $SEEGAYTE_MOUNTED ] && echo "SG not available/writable" && exit 0
# sudo rsync -avH --delete-during --delete-excluded /mnt/seegayte /mnt/bigboi/sg_backup;


