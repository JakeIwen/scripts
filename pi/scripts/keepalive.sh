#! /bin/bash

tasks=`ps ax`

bash /home/pi/scripts/internet_switches.sh

if [[ "$(ps ax)" != *'python3 server.py'* ]]  &> /dev/null 
then nohup python3 /home/pi/NativCast/server.py &
fi


