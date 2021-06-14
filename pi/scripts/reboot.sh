#!/bin/bash

if [[ -z `xset q | grep 'Monitor is On'` ]]; then 
  echo "Monitor is off, maintenance rebooting at $(date)"
  sudo reboot
fi
