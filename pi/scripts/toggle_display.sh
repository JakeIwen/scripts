#! /bin/bash

if [[ -z `xset q | grep 'Monitor is On'` ]]; then
  xset dpms force on
else
  xset dpms force off
fi
