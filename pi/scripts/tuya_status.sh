#! /bin/bash

name=$1 # starlink, cab_wiz, ext_flood, solder_flood
token=$(cat /home/pi/secrets/localtuya_token)

# assumes no duplicate names, otherwise provide full device_type_name ie switch.cab_wiz
if [[ "$name" == *.* ]]; then
  type_name=$name
else
  type_name=$(/home/pi/scripts/tuya_device_ids.sh | grep -P "\w+\.$name")
  num_devices=$(echo $type_name | wc -l)
  if [ $num_devices -ne 1 ]; then
    echo "multiple matching devices - aborting status check:\n$type_name"
    return 1
  fi
fi

curl -s -X GET \
  -H "Authorization: Bearer $token" \
  -H "Content-Type: application/json" \
  http://vanpi.local:8123/api/states/$type_name | jq -r .state