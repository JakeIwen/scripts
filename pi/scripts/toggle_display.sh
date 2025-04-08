#! /bin/bash
new_status=$1 # on, off

if [[ -n "$new_status" ]]; then
  xset dpms force $new_status
elif [[ -z `xset q | grep 'Monitor is On'` ]]; then
  xset dpms force on
else
  xset dpms force off
fi
