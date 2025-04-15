#! /bin/bash
isw="$HOME/scripts/internet_switches.sh"
tuya_toggle="$HOME/scripts/tuya_toggle.sh"
tuya_device_ids="$HOME/scripts/tuya_device_ids.sh"
mconf="$HOME/mconf"
log="$HOME/log/ignition_monitor.log"
nodisk() { rm $mconf/*; touch $mconf/nodisk; $isw; }

echo "ignition ON hook invoked"

stop_disks() {
  cp -R $mconf "${mconf}_last"
  nodisk
}


turn_lights_off() {
  $tuya_toggle ext_flood off &
  $tuya_toggle solder_flood off &
  # $tuya_toggle cab_wiz off &
  $tuya_device_ids | grep "^light." | while read -r line; do
    $tuya_toggle $line off &
  done
}

turn_lights_off &
stop_disks

echo "Ignition ON at $(date)" >> $log
echo "IGNITION ON DONE"