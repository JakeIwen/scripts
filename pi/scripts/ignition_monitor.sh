#! /bin/bash
# this script is run continuously by ignitionmonitor.service
num_confirmations_to_declare_off=8
scripts=/home/pi/scripts
hooks=/home/pi/hooks
histfile=/tmp/ignition_wifi_scan
inactive="$HOME/hooks/inactive/ignition"

> $histfile

# empty scan output means the scan itself failed (busy channel, driver hiccup);
# treat that as "unknown" rather than "network absent" to avoid false OFF flapping
van_ignition_on() {
  local scan
  scan=$(sudo iwlist wlan0 scan 2>/dev/null)
  if [ -z "$scan" ]; then echo unknown
  elif echo "$scan" | grep -q running_van_no_internet; then echo true
  else echo false
  fi
}

while :
do
  reading=$(van_ignition_on)
  if [ "$reading" = "unknown" ]; then sleep 3; continue; fi
  echo "$reading" >> $histfile

  ignition_was_on=$(test -f /home/pi/hooks/ignition_is_on && echo true || echo false)
  ignition_is_on="$(tail -1 $histfile)"
  script_inactive=$(test -f $inactive && echo true || echo false)

  if $ignition_is_on && ! $ignition_was_on && ! $script_inactive; then 
    echo "van ignition switched to ON"
    touch $hooks/ignition_is_on
    $hooks/ignition_on.sh # we rollin'

  elif ! $ignition_is_on && $ignition_was_on; then
    # ensure scan didnt just miss 1 reading before declaring van parked
    $scripts/last_n_lines_same.sh $histfile $num_confirmations_to_declare_off || continue

    echo "van ignition switched to OFF"
    rm $hooks/ignition_is_on
    $hooks/ignition_off.sh

  elif $ignition_is_on && $script_inactive; then
    # this will repeatedly run
    echo "van ignition monitor DEACTIVATED"
    rm $hooks/ignition_is_on
    $hooks/ignition_off.sh
  fi

  > $histfile
  sleep 3
done