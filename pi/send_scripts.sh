#! /bin/bash
dsc=~/dev/scripts

find $dsc/automation -type f -name "*.py" -exec cp {} $dsc/pi/scripts/python \;
scp -r $dsc/pi/scripts/ pi@192.168.6.103:/home/pi/
scp  $dsc/pi/.bashrc/ pi@192.168.6.103:/home/pi/.bashrc

