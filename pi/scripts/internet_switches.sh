#! /bin/bash
ubnt_internet_ops() {
  mount_drives
  start_torrent_client
}

mobile_internet_ops() {
  if cat /home/pi/mtorrent &> /dev/null 
  then ubnt_internet_ops
  else 
    kill_torrent_client
    if cat /home/pi/mdisk &> /dev/null 
    then mount_drives
    else unmount_drives
    fi
  fi
}


kill_torrent_client() {
  if [[ "$(ps ax)" == *"qbittorrent"* ]] 
  then echo 'killing torrent client'; pkill -TERM qbittorrent;
  fi
}

start_torrent_client() {
  if [[ "$(ps ax)" != *"qbittorrent"* ]] 
  then nohup qbittorrent-nox; echo 'started qbittorrent';
  fi
}

mount_drives() {
  sudo mount -a --options-source-force; echo "drives mounted"
}

unmount_drives() {
  for name in `cat /proc/self/mounts | grep -o '/dev/sd[^ ]*'`
  do echo "unmounting $name"; sudo umount $name
  done
}

kill_all() {
  kill_torrent_client
  unmount_drives
}
if date | grep '00:0'; then date; fi

if cat /home/pi/umdisk &> /dev/null; then kill_all
elif ping -c 1 172.20.10.3 &> /dev/null; then mobile_internet_ops
elif ping -c 1 8.8.8.8 &> /dev/null;     then ubnt_internet_ops
else no_internet_ops
fi
