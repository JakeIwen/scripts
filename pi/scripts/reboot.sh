#!/bin/bash

export DISPLAY=:0 # set display for non-user
if [[ -z `xset q | grep 'Monitor is On'` ]]; then 
  echo "Monitor is off, maintenance rebooting at $(date)"
  script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  "$script_dir/safe_reboot.sh"
else
  echo "Monitor is on, skipping scheduled reboot $(date)"
fi
