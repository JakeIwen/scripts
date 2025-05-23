#! /bin/bash

conf() { cat /home/pi/mconf/$1* &> /dev/null; }

ubnt_internet_ops() { # nanostation connected; van is likely stationary/parked
  echo 'ubnt_internet_ops'
  mount_drives
  sleep 1
  if conf notorrent || has_io_error '/mnt/movingparts'
  then kill_torrent_client 
  else start_torrent_client
  fi
}

has_io_error() { ls -lah "$1" 2>&1 | grep -q 'Input/output error'; }
# if has_io_error '/mnt/movingparts'; then echo 'i/o error'; fi

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

mobile_internet_ops() {
  echo 'mobile_internet_ops'
  if conf mtorrent; then ubnt_internet_ops; else no_internet_ops; fi
}

lifi_internet_ops() {
  echo 'lifi_internet_ops'
  if conf mtorrent_lifi; then
    echo "lifi-tor allowed"
    ubnt_internet_ops
  else
    no_internet_ops
  fi
}

no_internet_ops() {
  echo 'no_internet_ops'
  if conf mtorrent; then 
    mount_drives
    start_torrent_client
  elif conf mdisk; then 
    mount_drives
    kill_torrent_client
  else 
    kill_torrent_client
    unmount_drives
  fi 
    
  
}

kill_torrent_client() {
  if [[ "$(ps ax)" == *"qbittorrent"* ]]; then echo 'killtorrent' && pkill -TERM qbittorrent; fi
  sleep 2
  if [[ "$(ps ax)" == *"qbittorrent"* ]]; then echo 'SECOND ATTEMPT killtorrent' && pkill -f qbittorrent; fi
  sleep 2
}

start_torrent_client() {
  if [[ "$(grep movingparts /proc/mounts)" ]]; then 
    [[ "$(pgrep qbittor)" ]] || nohup qbittorrent-nox &
  else
    echo "preventing torrent-without-mpdisk"
    kill_torrent_client
  fi
}

mount_drives() {
  if [[ $(van_is_running) ]]; then
    echo "MOUNT interrupt: van is running, unmounting drives"
    echo "will not mount drives without idisk conf flag!"
    kill_torrent_client
    stop_service smbd 
    sleep 1
    unmount_drives
  else
    . /home/pi/scripts/mount_disks.sh
    sleep 3
    echo "drives mounted. starting smb share."
    start_service smbd 
  fi
}

van_is_running() {
  if test -f /home/pi/hooks/ignition_is_on; then
    [ -z "$(conf idisk)" ] && echo "yes"
  fi
}

spindown_drive() {
  uuid=$1
  echo "spinning down $uuid"
  sudo hd-idle -t "/dev/disk/by-uuid/$uuid" # spin-down drive
}


unmount_drives() {
  . /home/pi/scripts/umount_disks.sh
  sleep 5
  hdd_uuids=$(cat /home/pi/.disk_uuids | grep -Ev 'msd|usb' | cut -d' ' -f2)
  for loc in $hdd_uuids; do spindown_drive $loc; done
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
  sleep 4
  unmount_drives
}

ifaces="$(ssh root@OpenWrt 'mwan3 interfaces')"
iface_online() { echo "$ifaces" | grep "$1 is online"; }

if date | grep '0:0'; then date; fi
# ubnt_internet_ops
if conf nodisk &> /dev/null; then kill_all # drives disabled ~/mconf/nodisk
elif iface_online clientwan &> /dev/null; then mobile_internet_ops
elif iface_online lifiwan &> /dev/null; then lifi_internet_ops
elif iface_online wan &> /dev/null; then ubnt_internet_ops
else no_internet_ops
fi

