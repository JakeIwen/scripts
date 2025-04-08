#! /bin/bash

name=$1 # aux, cab_wiz, ext_flood, solder_flood
token=$(cat /home/pi/secrets/localtuya_token)

curl -s -X GET \
  -H "Authorization: Bearer $token" \
  -H "Content-Type: application/json" \
  http://vanpi.local:8123/api/states/switch.$name | jq -r .state