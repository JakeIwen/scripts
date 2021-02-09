x#! /bin/bash

/usr/sbin/service smbd stop
locations=`cat /proc/self/mounts | grep -o '/dev/sd[^ ]*'`
for loc in $locations; do
  echo "unmounting $loc"
  sudo umount $loc
done
