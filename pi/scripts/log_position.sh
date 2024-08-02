#! /bin/bash

POSPATH="/home/pi/vlc-positions.txt"

uridecode() { : "${*//+/ }"; echo -e "${_//%/\\x}"; } 

[[ -z "$(pgrep vlc)" ]] && exit 0
file=`python /home/pi/scripts/python-automation/vlc_property.py URL`
nsecs=`python /home/pi/scripts/python-automation/vlc_property.py NS`
if [[ $file && $nsecs ]]; then
  decoded="`uridecode $file`"
  trimmed="`echo $decoded | sed -E 's|.*\/links||g'`"
  sed -i "\|^$trimmed|d" $POSPATH
  echo "$trimmed $nsecs" >> $POSPATH
  echo "$trimmed $nsecs"
else
  echo "could not extract position on file: $file"
fi
