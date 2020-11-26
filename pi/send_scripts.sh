#! /bin/bash
dsc=~/dev/scripts

mkdir $dsc/pi/scripts/python/ 
find $dsc/automation -type f -name "*.py" -exec cp {} $dsc/pi/scripts/python/ \;

scp -r $dsc/pi/scripts/ pi@192.168.6.103:/home/pi/
scp  $dsc/pi/.bashrc/ pi@192.168.6.103:/home/pi/.bashrc
scp  $dsc/pi/sns.sh/ pi@192.168.6.103:/home/pi/sns.sh

rm -R $dsc/pi/scripts/python/

