#! /bin/bash
ubnt_internet_ops() {
  mount_drives
  start_torrent_client
}

conf() {
  cat /home/pi/mconf/$1 &> /dev/null 
}

mobile_internet_ops() {
  if conf mtorrent
  then ubnt_internet_ops
  else no_internet_ops
  fi
}

no_internet_ops() {
  kill_torrent_client
  if conf mdisk
  then mount_drives
  else unmount_drives
  fi
}

kill_torrent_client() {
  if [[ "$(ps ax)" == *"qbittorrent"* ]] 
  then echo 'killing torrent client'; pkill -TERM qbittorrent;
  fi
}

start_torrent_client() {
  if [[ "$(ps ax)" != *"qbittorrent"* ]] 
  then echo 'started qbittorrent'; nohup qbittorrent-nox &
  fi
}

mount_drives() {
  exit 0;
  /home/pi/scripts/mount_all.sh
  echo "drives mounted. sharting smb share."
  start_service smbd 
}

unmount_drives() {
  stop_service smbd 
  locations=`cat /proc/self/mounts | grep -o '/dev/sd[^ ]*'`
  for loc in $locations; do
    echo "unmounting $loc"
    sudo umount $loc
  done
  sleep 5
  for loc in $locations; do spindown_drive $loc; done
}

spindown_drive() {
  loc=$1
  name="${loc/\/dev\//}"
  echo "spinning down $name"
  sudo hd-idle -t "$name" # spin-down drive
}

stop_service() {
  /usr/sbin/service $1 stop
}

start_service() {
  /usr/sbin/service $1 status > /dev/null || /usr/sbin/service $1 start
}

kill_all() {
  kill_torrent_client
  unmount_drives
}


if date | grep '00:0'; then date; fi

if conf nodisk &> /dev/null; then kill_all
elif ping -c 1 172.20.10.1 &> /dev/null; then mobile_internet_ops
elif ping -c 1 8.8.8.8 &> /dev/null;     then ubnt_internet_ops
else no_internet_ops
fi
