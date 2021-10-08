#! /bin/bash

# run as root
bscan() {
  pkill -f "hcitool lescan"
  systemctl restart bluetooth
  sleep 0.5
  stdbuf -oL hcitool lescan &
  PID=$!
  sleep 3
  kill -9 $PID
}

fscan() {
  exec 3< <(bscan)
  results=$(cat <&3)
  echo $results | grep -Eo "(\w\w\:){5}\w\w" | sort -u
}

fscan

