#! /bin/bash

airsonos() {
  if [[ `ps ax` != *'airupnp-arm'* ]] &> /dev/null 
  then echo "process down, starting airsonos" && sudo service airupnp start
  fi
}

nativcast() {
  if [[ `ps ax` != *'server.py'* ]] &> /dev/null 
  then echo "process down, starting nativcast" &&  python3 /home/pi/NativCast/NativCast.sh
  fi
}

van_ignition_monitor() {
  if [[ `ps ax` != *'ignition_monitor'* ]] &> /dev/null 
  then echo "process down, starting van_ignition_monitor" &&  /home/pi/hooks/ignition_monitor.sh & 
  fi
}

echo "start: $(date)"
echo "airsonos check start"
airsonos
echo "nativcast check start"
nativcast
echo "van_ignition_monitor check start"
van_ignition_monitor
echo "finished: $(date)"
echo ""
  