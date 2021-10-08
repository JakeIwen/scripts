#! /bin/bash

# called via some noip config thing
echo "public ip changed. $# args provided" >> /home/pi/log/public_ip_changed.log
exit 0