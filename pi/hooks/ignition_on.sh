#! /bin/bash
isw="$HOME/scripts/internet_switches.sh"
tuya_toggle="$HOME/scripts/tuya_toggle.sh"
tuya_device_ids="$HOME/scripts/tuya_device_ids.sh"
inactive="$HOME/hooks/inactive/ignition"
log="$HOME/log/ignition_monitor.log"


turn_lights_off() {
  # COP ALERT now owns GPIO17; exterior Tuya power is ordinary lighting.
  "$tuya_toggle" ext_flood off &
  $tuya_toggle solder_flood off & # switches
  # $tuya_toggle cab_wiz off &
  $tuya_device_ids | grep "^light." | while read -r line; do
    $tuya_toggle $line off & # wiz lights
  done
}

echo "" >> "$log"
echo "$(date)" >> "$log"
echo "Ignition ON script invoked" >> "$log"

if [[ -f "$inactive" ]]; then
  echo "(ignition monitor INACTIVE)" >> "$log"
else
  echo "ignition monitor ACTIVE" >> "$log"
  turn_lights_off &
  if "$isw"; then
    echo "IGNITION ON DONE" >> "$log"
  else
    rc=$?
    echo "IGNITION ON policy failed with status $rc" >> "$log"
    exit "$rc"
  fi
fi
