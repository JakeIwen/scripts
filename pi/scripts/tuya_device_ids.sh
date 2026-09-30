#! /bin/bash

token=$(cat /home/pi/secrets/localtuya_token)

curl -s GET -H "Authorization: Bearer $token" http://vanpi.lan:8123/api/states | jq -r '.[].entity_id' | sort