#! /bin/bash
isw="$HOME/scripts/internet_switches.sh"
tuya_toggle="$HOME/scripts/tuya_toggle.sh"
tuya_device_ids="$HOME/scripts/tuya_device_ids.sh"
cop_alert_ext_flood_guard="$HOME/scripts/cop_alert_ext_flood_guard.sh"
inactive="$HOME/hooks/inactive/ignition"
mconf="$HOME/mconf"
log="$HOME/log/ignition_monitor.log"

nodisk() { rm $mconf/*; touch $mconf/nodisk; $isw; }

stop_disks() {
  cp -R $mconf "${mconf}_last"
  nodisk
}


turn_lights_off() {
  if "$cop_alert_ext_flood_guard" >> "$log" 2>&1; then
    "$tuya_toggle" ext_flood off & # safe: inactive, stale, or engine confirmed running via C-CAN
  else
    echo "COP ALERT preserved ext_flood (no fresh C-CAN engine-running evidence)" >> "$log"
  fi
  $tuya_toggle solder_flood off & # switches
  # $tuya_toggle cab_wiz off &
  $tuya_device_ids | grep "^light." | while read -r line; do
    $tuya_toggle $line off & # wiz lights
  done
}

echo "" >> $log
echo "$(date)" >> $log
echo "Ignition ON script invoked" >> $log

if cat $inactive; then
  echo "(ignition monitor INACTIVE)" >> $log
else
  echo "ignition monitor ACTIVE" >> $log
  turn_lights_off &
  stop_disks
  echo "IGNITION ON DONE" >> $log
fi
