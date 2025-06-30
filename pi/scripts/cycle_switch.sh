#! /bin/bash
# for AIMS 24VDC AC/DC charger troubleshoooting, can probably delete
isw="$HOME/scripts/internet_switches.sh"
tuya_toggle="$HOME/scripts/tuya_toggle.sh"
tuya_device_ids="$HOME/scripts/tuya_device_ids.sh"
log="$HOME/log/switch_cycle.log"

write_log() {
  echo "$1" >> $log
  echo "$1"
}

cycle() {
  switch_name="$1"
  write_log "turning off $switch_name"
  $tuya_toggle "$switch_name" off
  sleep 5
  write_log "turning on $switch_name"
  $tuya_toggle "$switch_name" on
  sleep 120
}

while true; do cycle ext_flood; done
