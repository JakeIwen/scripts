#! /bin/bash

if [[ "$#" = "1" ]]
  then disk_name=$1
else
  unset disk_name
fi

kill_torrent_client() {
  if [[ "$(ps ax)" == *"qbittorrent"* ]]; then echo 'killtorrent' && sudo pkill -TERM qbittorrent; fi
  sleep 4
  if [[ "$(ps ax)" == *"qbittorrent"* ]]; then echo 'SECOND ATTEMPT killtorrent' && sudo pkill -f qbittorrent; fi
  sleep 3
}

kill_torrent_client

locations() { 
  if [ -z ${disk_name+x} ]; then
    cat /proc/self/mounts | grep -Po "/mnt/[^ ]+"
  else
    cat /proc/self/mounts | grep -Po "/mnt/${disk_name}[^_]"
  fi
}

locs=`locations`
echo "$locs"
if [[ -n "$locs" ]]; then 
  sudo service smbd stop
  echo "unmounting $locs"
  sudo umount $locs
fi

locs=`locations`
if [[ -n "$locs" ]]; then 
  echo "forcefully unmounting $locs"
  sudo umount $locs -fl
fi

