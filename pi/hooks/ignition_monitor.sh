#! /bin/bash

van_bt_mac="00:54:AF:5A:58:2F"
histfile=/tmp/bt_name
true > $histfile
sleep 1


while :
do
  sleep 1
  flag_file_present=$(test -f /home/pi/hooks/ignition_is_on && echo "yes")
  found_device=$(hcitool name $van_bt_mac)
  name=${found_device:-none}
  echo "$name" >> $histfile
  echo "name: $name"
  
  if [ "$name" != "none" ] && [ -z "$flag_file_present" ]; then 
    echo "van switched to ON"
    touch /home/pi/hooks/ignition_is_on
    /home/pi/hooks/ignition_on.sh # we rollin'
    continue
  fi
  
  num_recent_uniq=`tail -5 $histfile | sort -u | wc -l`
  
  if [ "$name" = "none" ] && [ "$num_recent_uniq" -eq 1 ] && [ -n "$flag_file_present" ]; then
    echo "van switched to OFF"
    rm /home/pi/hooks/ignition_is_on
    /home/pi/hooks/ignition_off.sh # no smog time
  fi
  
done

# Tip: To automate bluetoothctl commands, use 
# echo -e "command1\ncommand2\n" | bluetoothctl or bluetoothctl -- command.

# echo -e "info 00:54:AF:5A:58:2F" | bluetoothctl
