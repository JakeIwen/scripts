#! /bin/bash

airsonos() {
  if [[ `ps ax` != *'airupnp-arm'* ]] &> /dev/null; then 
    echo "process down, starting airsonos" 
    /home/pi/airupnp-arm -z
  fi
}

is_active() { grep -P "^$1" /home/pi/keepalive.txt; }

echo "start: $(date)"
is_active airsonos && airsonos
echo -e "done\n"
