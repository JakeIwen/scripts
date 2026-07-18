#! /bin/bash
isw="$HOME/scripts/internet_switches.sh"
log="$HOME/log/ignition_monitor.log"

echo "" >> "$log"
echo "$(date)" >> "$log"
echo "ignition OFF hook invoked" >> "$log"

if "$isw"; then
  echo "Ignition OFF at $(date)" >> "$log"
  echo "VAN OFF DONE"
else
  rc=$?
  echo "Ignition OFF policy failed with status $rc" >> "$log"
  exit "$rc"
fi
