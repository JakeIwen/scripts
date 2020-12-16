#! /bin/bash

if [[ `ps ax` != *'NativCast/server.py'* ]] &> /dev/null 
then 
  python3 /home/pi/NativCast/server.py
  python3 -c "from sonos_tasks import rear_movie; rear_movie(70)"
fi


