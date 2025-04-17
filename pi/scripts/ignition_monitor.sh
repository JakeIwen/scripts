#! /bin/bash
# this script is run continuously by ignitionmonitor.service
num_confirmations_to_declare_off=3
scripts=/home/pi/scripts
hooks=/home/pi/hooks
histfile=/tmp/ignition_wifi_scan
> $histfile

uconnect_wifi_available() { sudo iwlist wlan0 scan | grep running_van_no_internet; }
van_ignition_on(){ [ "$(uconnect_wifi_available)" ] && echo true || echo false; }

while :
do
  van_ignition_on >> $histfile

  ignition_was_on=$(test -f /home/pi/hooks/ignition_is_on && echo true || echo false)
  ignition_is_on="$(tail -1 $histfile)"

  if $ignition_is_on && ! $ignition_was_on; then 
    echo "van switched to ON"
    touch $hooks/ignition_is_on
    $hooks/ignition_on.sh # we rollin'

  elif ! $ignition_is_on && $ignition_was_on; then
    # ensure scan didnt just miss 1 reading before declaring van parked
    $scripts/last_n_lines_same.sh $histfile $num_confirmations_to_declare_off || continue

    echo "van switched to OFF"
    rm $hooks/ignition_is_on
    $hooks/ignition_off.sh
  fi

  > $histfile
  sleep 3
done