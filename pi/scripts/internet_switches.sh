#! /bin/bash

# The ignition hook can invoke this at the same time as the minutely cron job.
# Serialize the entire policy decision so mounting and unmounting cannot race.
exec 9>/home/pi/.internet_switches.lock || exit 1
if ! /usr/bin/flock -w 55 9; then
  echo "another internet_switches.sh instance held the lock for 55 seconds"
  exit 1
fi

conf() { cat /home/pi/mconf/$1* &> /dev/null; }

ubnt_internet_ops() { # nanostation connected; van is likely stationary/parked
  echo 'ubnt_internet_ops'
  mount_drives
  sleep 1
  if conf notorrent || has_io_error '/mnt/movingparts' || starlink_notor
  then kill_torrent_client 
  else start_torrent_client
  fi
}

starlink_notor() {
  local status=$(/home/pi/scripts/tuya_status.sh starlink)
  if [ "$status" = "on" ]; then
    ls /home/pi/starconf/notor &> /dev/null
  else
    ls /home/pi/starconf/idkhowtoreturnfalse &> /dev/null
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
    if ! pgrep qbittor >/dev/null; then
      # Background only qbittorrent—not the surrounding conditional—and do
      # not let the long-lived client inherit fd 9 and hold our flock.
      nohup qbittorrent-nox 9>&- &
    fi
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
    /home/pi/scripts/umount_disks.sh --clear-spindown-state || return 1
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

unmount_drives() {
  /home/pi/scripts/umount_disks.sh --spindown
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

iface_online() { 
  ifaces="$(ssh root@OpenWrt 'mwan3 interfaces')"
  echo "$ifaces" | grep "interface $1 is online"
}

# if date | grep '0:0'; then date; fi
# ubnt_internet_ops
set_isw_options() {
  echo ""
  echo "$(date)"
  if conf nodisk &> /dev/null; then kill_all # drives disabled ~/mconf/nodisk
  elif iface_online clientwan &> /dev/null; then mobile_internet_ops
  elif iface_online lifiwan &> /dev/null; then lifi_internet_ops
  elif iface_online wan &> /dev/null; then ubnt_internet_ops
  else no_internet_ops
  fi
}
# 
set_isw_options
