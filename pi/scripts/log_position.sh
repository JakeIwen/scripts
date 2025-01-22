#! /bin/bash

POSPATH="/home/pi/vlc-positions.txt"

uridecode() { : "${*//+/ }"; echo -e "${_//%/\\x}"; } 

function hhmmss {
  local X=$1
  local T=$((X/1000/1000))
  local H=$((T/60/60))
  local M=$((T/60%60))
  local S=$((T%60))
  printf '%02d:' $H
  printf '%02d:' $M
  printf '%02d' $S
}

[[ -z "$(pgrep vlc)" ]] && exit 0
file=`python /home/pi/scripts/python-automation/vlc_property.py URL`
nsecs=`python /home/pi/scripts/python-automation/vlc_property.py NS`
if [[ $file && $nsecs ]]; then
  decoded="`uridecode $file`"
  trimmed="`echo $decoded | sed -E 's|.*\/links||g'`"
  sed -i "\|^$trimmed|d" $POSPATH
  line="$trimmed $nsecs $(hhmmss $nsecs)"
  echo "$line" >> $POSPATH
  sed -i '/^[^\/]/ d' $POSPATH # rm any python errors sent to file
  echo "$line"
else
  echo "could not extract position on file: $file"
fi
