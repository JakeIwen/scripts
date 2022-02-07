#! /bin/bash

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
