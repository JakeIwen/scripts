#! /bin/bash
isw="$HOME/scripts/internet_switches.sh"
tuya_toggle="$HOME/scripts/tuya_toggle.sh"
tuya_device_ids="$HOME/scripts/tuya_device_ids.sh"
inactive="$HOME/hooks/inactive/ignition"
mconf="$HOME/mconf"
log="$HOME/log/ignition_monitor.log"

nodisk() { rm $mconf/*; touch $mconf/nodisk; $isw; }

stop_disks() {
  cp -R $mconf "${mconf}_last"
  nodisk
}


turn_lights_off() {
  $tuya_toggle ext_flood off & # switches
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
