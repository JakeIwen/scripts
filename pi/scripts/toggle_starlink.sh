#! /bin/bash
# optional arg: "on" or "off"
tuya_toggle="$HOME/scripts/tuya_toggle.sh"
tuya_status="$HOME/scripts/tuya_status.sh"

play_sound() {
  bash ~/sns.sh play_soundbyte $1
}

set_starlink_access_point() {
  ssh -i ~/.ssh/id_rsa ubnt@192.168.8.20 '. ~/.profile && set_ap denlink' > /dev/null
}

orig_status="$($tuya_status starlink)" > /dev/null

# if no change in status
if [ "$orig_status" = "$1" ]; then 
  play_sound warn
  exit 0
fi

new_status="$($tuya_toggle starlink $1)" > /dev/null

if [ "$new_status" = "on" ]; then 
  set_starlink_access_point & > /dev/null
  play_sound success
elif [ "$new_status" = "off" ]; then 
  play_sound deactivate
fi

echo "$new_status"