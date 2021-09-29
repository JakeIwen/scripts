#! /bin/bash

sudo service smbd stop
locations() { cat /proc/self/mounts | grep -o '/mnt/[^ ]+'; }

locs=`locations`
if [[ -n "$locs" ]]; then 
  echo "unmounting $locs"
  sudo umount $locs
fi

locs=`locations`
if [[ -n "$locs" ]]; then 
  echo "forcefully unmounting $locs"
  sudo umount $locs -fl
fi
