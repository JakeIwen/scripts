#! /bin/bash

if [[ "$#" = "1" ]]
  then disk_name=$1
else
  unset disk_name
fi

kill_torrent_client() {
  if [[ "$(ps ax)" == *"qbittorrent"* ]]; then echo 'killtorrent' && sudo pkill -TERM qbittorrent && sleep 4; fi
  if [[ "$(ps ax)" == *"qbittorrent"* ]]; then echo 'SECOND ATTEMPT killtorrent' && sudo pkill -f qbittorrent && sleep 3; fi
}

graceful_smb_shutdown() {
  # Signal Time Machine shares closed so macOS can finalize the sparsebundle
  # before smbd receives SIGTERM. Without this, the sparsebundle is left locked
  # and macOS reports a spurious "username or password" error on next connect.
  for share in mbp1tbkup mbp2tbkup; do
    sudo smbcontrol smbd close-share "$share" 2>/dev/null && echo "closed SMB share: $share"
  done
  sleep 2  # allow in-flight writes to drain
}

hdd_locations() { 
  if [ -z ${disk_name+x} ]; then
    cat /proc/self/mounts | grep -Po "/mnt/[^ ]+" | grep -vP 'usb|msd'
  else
    cat /proc/self/mounts | grep -Po "/mnt/${disk_name}[^_]"
  fi
}

locs=`hdd_locations`
echo "$locs"
if [ -n "$(echo $locs | grep -Po movingparts)" ]; then
  kill_torrent_client
fi

if [[ -n "$locs" ]]; then
  graceful_smb_shutdown
  sudo service smbd stop
  echo "unmounting $locs"
  sudo umount $locs
fi

locs=`hdd_locations`
if [[ -n "$locs" ]]; then 
  echo "forcefully unmounting $locs"
  sudo umount $locs -fl
fi

echo "mounted disks:"
grep "dev/sd" /proc/mounts

