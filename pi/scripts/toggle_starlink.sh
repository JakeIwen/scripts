#! /bin/bash
# optional arg: "on" or "off"
tuya_toggle="$HOME/scripts/tuya_toggle.sh"
# tuya_status="$HOME/scripts/tuya_status.sh"

status="$($tuya_toggle starlink $1)" > /dev/null

set_starlink_access_point() {
  ssh -i ~/.ssh/id_rsa ubnt@192.168.8.20 '. ~/.profile && set_ap denlink' > /dev/null
}

if [ "$status" = "on" ]; then 
  set_starlink_access_point & > /dev/null
fi

echo "$status"