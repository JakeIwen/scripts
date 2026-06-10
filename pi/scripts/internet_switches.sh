#! /bin/bash

# Serialize instances: a minutely-cron run mid-mount must not interleave with
# an ignition-hook run trying to unmount (or vice versa). Lock is held for the
# life of the process via fd 200.
exec 200>/tmp/internet_switches.lock
flock -w 90 200 || { echo "isw lock timeout; exiting"; exit 1; }

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
  if [[ "$(ps ax)" == *"qbittorrent"* ]]; then
    echo 'killtorrent' && pkill -TERM qbittorrent
    sleep 2
    if [[ "$(ps ax)" == *"qbittorrent"* ]]; then echo 'SECOND ATTEMPT killtorrent' && pkill -f qbittorrent; fi
    sleep 2
  fi
}

start_torrent_client() {
  if [[ "$(grep movingparts /proc/mounts)" ]]; then 
    [[ "$(pgrep qbittor)" ]] || nohup qbittorrent-nox &
  else
    echo "preventing torrent-without-mpdisk"
    kill_torrent_client
  fi
}

clear_stale_tm_locks() {
  # Remove stale sparsebundle lock state ('lock'/'token' files) left by
  # interrupted backups, which cause "backup already in use" on next attempt.
  # NOT mapped/ — that is persistent band metadata, deleting it kills the bundle.
  # Only safe while smbd is down: a live backup legitimately holds its lock.
  if pgrep smbd > /dev/null; then return 0; fi
  for mount in /mnt/mbp1tbkup /mnt/mbp2tbkup; do
    find "$mount" -maxdepth 1 -name "*.sparsebundle" -type d 2>/dev/null | while read -r sb; do
      stale=$(ls "$sb/lock" "$sb/token" 2>/dev/null)
      if [ -n "$stale" ]; then
        echo "STALE TM lock state found (interrupted backup?):"
        echo "$stale"
        sudo rm -f "$sb/lock" "$sb/token"
      fi
    done
  done
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
    if [[ $(van_is_running) ]]; then
      # ignition came on mid-mount; the hook's isw instance is queued on the
      # lock behind us, but don't hand it a running smbd to tear down
      echo "MOUNT abort: ignition came on mid-mount, unmounting"
      unmount_drives
      return 1
    fi
    clear_stale_tm_locks
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
  /home/pi/scripts/umount_disks.sh
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