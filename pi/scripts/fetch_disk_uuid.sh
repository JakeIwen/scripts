#! /bin/bash

cat /home/pi/.disk_uuids | grep "^$1 " | cut -d' ' -f2