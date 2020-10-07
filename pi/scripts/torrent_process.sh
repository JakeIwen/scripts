#! /bin/bash
if ping -c 1 172.20.10.3 &> /dev/null 
then
  echo 'connected to phone'
  if cat /home/pi/mtorrent &> /dev/null 
  then 
    echo 'phone torrent allowed'
  else
    echo 'killing torrent client'
    pkill -TERM qbittorrent
    exit 0
  fi
  
elif ping -c 1 8.8.8.8 &> /dev/null
then
  STR=$(ps ax)
  SUB='qbittorrent'

  if [[ "$STR" != *"$SUB"* ]]; then
    echo 'starting qbittorrent'
    export DISPLAY=:0;
    qbittorrent-nox &
    exit 0
  else
    echo 'qbit already running'
  fi
fi


