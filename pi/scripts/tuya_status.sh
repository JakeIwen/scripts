#!/bin/bash

name=${1:-} # starlink, cab_wiz, ext_flood, solder_flood
token=$(cat /home/pi/secrets/localtuya_token)

if [[ -z "$name" ]]; then
  echo "usage: tuya_status.sh <entity name>" >&2
  exit 1
fi

# assumes no duplicate names, otherwise provide full device_type_name ie switch.cab_wiz
if [[ "$name" == *.* ]]; then
  type_name=$name
else
  type_name=$(/home/pi/scripts/tuya_device_ids.sh | grep -P "\w+\.$name" || true)
  num_devices=$(printf '%s\n' "$type_name" | grep -c .)
  if [ $num_devices -ne 1 ]; then
    printf 'expected one matching device, found %s:\n%s\n' "$num_devices" "$type_name" >&2
    exit 1
  fi
fi

if ! response=$(/usr/bin/curl -fsS --max-time 15 -X GET \
  -H "Authorization: Bearer $token" \
  -H "Content-Type: application/json" \
  "http://vanpi.local:8123/api/states/$type_name"); then
  echo "failed to read $type_name" >&2
  exit 1
fi

printf '%s' "$response" | /usr/bin/jq -er '.state'
