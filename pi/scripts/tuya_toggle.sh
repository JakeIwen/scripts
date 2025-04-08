#! /bin/bash

name=$1 # aux, cab_wiz, ext_flood, solder_flood
to_state=$2 # on, off, blank means toggle
token=$(cat /home/pi/secrets/localtuya_token)

if [[ -z "$to_state" ]]; then
  state=$(/home/pi/scripts/tuya_status.sh $name)
  [[ "$state" == "on" ]] && to_state=off || to_state=on
fi

curl -X POST \
  -H "Authorization: Bearer $token" \
  -H "Content-Type: application/json" \
  -d "{\"entity_id\": \"switch.$name\"}" \
  http://vanpi.local:8123/api/services/switch/turn_$to_state
