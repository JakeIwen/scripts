#! /bin/bash
isw="$HOME/scripts/internet_switches.sh"
tuya_toggle="$HOME/scripts/tuya_toggle.sh"
mconf="$HOME/mconf"
log="$HOME/log/ignition_monitor.log"
nodisk() { rm $mconf/*; touch $mconf/nodisk; $isw; }

echo "ignition ON hook invoked"

cp -R $mconf "${mconf}_last"
nodisk

$tuya_toggle ext_flood off

echo "Ignition ON at $(date)" >> $log
echo "IGNITION ON DONE"