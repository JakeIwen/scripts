#! /bin/bash
ubnt_internet_ops() {
  echo 'ubnt_internet_ops'
  mount_drives
  if conf notorrent; then exit 0; else start_torrent_client; fi
}

is_online() {
  [[ `ssh root@OpenWrt "cat /tmp/run/mwan3track/$1/ONLINE"` > 0 ]]
}

update_iface_score() {
  file=$1
  iface_score=`cat $file`
  if [ "$iface_score" -lt "$lowest_score" ]; then
    echo "IS LES"
    lowest_score=$iface_score
    iface=`basename "$(dirname $file)"`
    echo "set iface. l: $lowest_score, f: $file, iface: $iface"
  fi
}

conf() {
  cat /home/pi/mconf/$1* &> /dev/null
}

mobile_internet_ops() {
  echo 'mobile_internet_ops'
  if conf mtorrent; then ubnt_internet_ops; else no_internet_ops; fi
}

lifi_internet_ops() {
  echo 'lifi_internet_ops'
  if [ -e /home/pi/mconf/mtorrent_lifi ]; then
    echo "lifi-tor allowed"
    ubnt_internet_ops
  else
    no_internet_ops
  fi
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
  if [ -e /mnt/movingparts/ext/airupnp-arm ]; then 
    [[ "$(ps ax)" != *"qbittorrent"* ]] && nohup qbittorrent-nox
  else
    echo "preventing torrent-without-mpdisk"
    kill_torrent_client
  fi
}

mount_drives() {
  /home/pi/scripts/mount_all.sh &> /dev/null
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
  sudo /usr/sbin/service $1 stop
}

start_service() {
  /usr/sbin/service $1 status > /dev/null || sudo /usr/sbin/service $1 start
}

kill_all() {
  echo 'killing all'
  kill_torrent_client
  unmount_drives
}

if date | grep '0:0'; then date; fi

if conf nodisk &> /dev/null; then kill_all
elif is_online clientwan &> /dev/null; then mobile_internet_ops
elif is_online lifiwan &> /dev/null; then lifi_internet_ops
elif ping -c 1 8.8.8.8 &> /dev/null; then ubnt_internet_ops
else no_internet_ops
fi

