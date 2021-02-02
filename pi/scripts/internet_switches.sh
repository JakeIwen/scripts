#! /bin/bash
ubnt_internet_ops() {
  echo 'ubnt_internet_ops'
  mount_drives
  if conf notorrent; then exit 0; else start_torrent_client; fi
}

conf() {
  cat /home/pi/mconf/$1 &> /dev/null 
}

mobile_internet_ops() {
  echo 'mobile_internet_ops'
  if conf mtorrent; then ubnt_internet_ops; else no_internet_ops; fi
}

no_internet_ops() {
  echo 'no_internet_ops'
  kill_torrent_client
  if conf mdisk; then mount_drives; else unmount_drives; fi
}

kill_torrent_client() {
  if [[ "$(ps ax)" == *"qbittorrent"* ]]; then echo 'killtorrent' && pkill -TERM qbittorrent; fi
}

start_torrent_client() {
  if [[ "$(ps ax)" != *"qbittorrent"* ]]; then nohup qbittorrent-nox; fi
}

mount_drives() {
  /home/pi/scripts/mount_all.sh
  /home/pi/scripts/fix_hfs_fs.sh
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
  echo 'killing all'
  kill_torrent_client
  unmount_drives
}


if date | grep '0:0'; then date; fi

if conf nodisk &> /dev/null; then kill_all
elif ping -c 1 172.20.10.1 &> /dev/null; then mobile_internet_ops
elif ping -c 1 8.8.8.8 &> /dev/null;     then ubnt_internet_ops
else no_internet_ops
fi
