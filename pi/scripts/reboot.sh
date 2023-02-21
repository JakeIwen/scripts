#!/bin/bash

export DISPLAY=:1 # set display for non-user
if [[ -z `xset q | grep 'Monitor is On'` ]]; then 
  echo "Monitor is off, maintenance rebooting at $(date)"
  sudo reboot
else
  echo "Monitor is on, skipping scheduled reboot $(date)"
fi
