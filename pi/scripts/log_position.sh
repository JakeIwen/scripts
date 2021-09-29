#! /bin/bash

POSPATH="/home/pi/vlc-positions.txt"

[[ -z "$(pgrep vlc)" ]] && exit 0
file=`python /home/pi/scripts/python/vlc_property.py URL`
nsecs=`python /home/pi/scripts/python/vlc_property.py NS`
if [[ $file && $nsecs ]]; then
  sed -i "\|^$file|d" $POSPATH
  echo "$file $nsecs" >> $POSPATH
  echo "$file $nsecs"
else
  echo "could not extract position on file: $file"
fi
