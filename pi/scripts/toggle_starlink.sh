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

new_status="$1"
orig_status="$($tuya_status starlink)" > /dev/null

if [ "$orig_status" = "$new_status" ]; then 
  play_sound warn
  exit 0
fi

$tuya_toggle starlink $new_status

if [ "$new_status" = "on" ]; then 
  set_starlink_access_point & > /dev/null
  play_sound poweron
elif [ "$new_status" = "off" ]; then 
  play_sound deactivate
fi

echo "starlink set to $new_status"

exit 0