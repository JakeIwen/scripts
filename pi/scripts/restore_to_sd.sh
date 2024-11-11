#! /bin/bash

s() { name=$1; shift; /home/pi/scripts/$name.sh "$@"; }

target=$1 # ie sda

if [ $(fsprop LABEL_FATBOOT $target) != 'bootfs' ]; then
  echo "incorrectly formatted drive." 
  echo "please flash sd card toworking stock RPi x64 first"
  exit || return
fi

s dd_restore_root $target

s rsync_restore_firmware /mnt/bigboi/pi_backup_git/dd_mmcblk0p1 /dev/${target}1

