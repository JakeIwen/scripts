#! /bin/bash
isw="$HOME/scripts/internet_switches.sh"
mconf="$HOME/mconf"
log="$HOME/log/ignition_monitor.log"

echo ""
echo "$(date)"
echo "ignition OFF hook invoked"

if [ -d "${mconf}_last" ]; then
  echo "moving mconf_last dir to mconf, running ISW script" >> $log
  rm -rf $mconf
  mv "${mconf}_last" $mconf 
  $isw
else
  echo "no mconf_last dir, NO ACTION" >> $log
fi
echo "Ignition OFF at $(date)" >> $log
echo "VAN OFF DONE"
