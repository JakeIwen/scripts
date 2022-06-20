#! /bin/bash

airsonos() {
  if [[ `ps ax` != *'airupnp-arm'* ]] &> /dev/null; then 
    echo "process down, starting airsonos" 
    /home/pi/airupnp-arm -z
  fi
}

nativcast() {
  if [[ `ps ax` != *'server.py'* ]] &> /dev/null; then 
    echo "process down, starting nativcast" 
    cd /home/pi/NativCast/ || exit
    sudo ./NativCast.sh stop
    ./NativCast.sh start & 
  fi
}

van_ignition_monitor() {
  if [[ `ps ax` != *'ignition_monitor'* ]] &> /dev/null; then 
    echo "process down, starting van_ignition_monitor" 
    /home/pi/hooks/ignition_monitor.sh & 
  fi
}

is_active() {
  grep -P "^$1" /home/pi/keepalive.txt
}

echo "start: $(date)"
is_active airsonos && airsonos
is_active nativcast && nativcast
is_active van_ignition_monitor && van_ignition_monitor
echo ""
  