#! /bin/bash
if ! pgrep python3; then 
  echo 'starting cast'
  date
  pkill -f server.py
  lsof -ti:2020 | xargs kill
  cd /home/pi/NativCast || exit
  ./NativCast.sh start
fi