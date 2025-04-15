#! /bin/bash
# tuya_toggle.sh aux on

entity=$1 # aux, cab_wiz, ext_flood, solder_flood, light.dresser
to_state=$2 # on, off, blank means toggle
token=$(cat /home/pi/secrets/localtuya_token)

if [[ -z "$entity" ]]; then
  echo 'enter a device entity/name'
  echo 'entity=$1 # aux, cab_wiz, ext_flood, solder_flood, light.dresser'
  echo 'to_state=$2 # on, off, blank means toggle'
  
  if return 2>/dev/null; then return 1; else exit 1; fi
fi

if [[ "$entity" == *.* ]]; then
  type_name=$entity
else
  type_name="switch.$entity"
fi

IFS='.' read -r type name <<< "$type_name"

if [[ -z "$to_state" ]]; then
  state=$(/home/pi/scripts/tuya_status.sh $name)
  [[ "$state" == "on" ]] && to_state=off || to_state=on
fi

curl -X POST \
  -H "Authorization: Bearer $token" \
  -H "Content-Type: application/json" \
  -d "{\"entity_id\": \"$type_name\"}" \
  http://vanpi.local:8123/api/services/$type/turn_$to_state
