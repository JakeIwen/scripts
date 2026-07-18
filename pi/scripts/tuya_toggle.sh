#!/bin/bash
# tuya_toggle.sh aux on
log="$HOME/log/tuya_toggle.log"
entity=${1:-} # starlink, cab_wiz, ext_flood, solder_flood, light.dresser, starlink
to_state=${2:-} # on, off, blank means toggle
token=$(cat /home/pi/secrets/localtuya_token)

write_log() {
  echo "$1" >> $log
  echo "$1"
}

if [[ -z "$entity" ]]; then
  echo 'enter a device entity/name'
  echo 'entity=$1 # starlink, cab_wiz, ext_flood, solder_flood, light.dresser'
  echo 'to_state=$2 # on, off, blank means toggle'
  
  if return 2>/dev/null; then return 1; else exit 1; fi
fi

if [[ -n "$to_state" && "$to_state" != "on" && "$to_state" != "off" ]]; then
  write_log "invalid state '$to_state' (expected on or off)"
  if return 2>/dev/null; then return 1; else exit 1; fi
fi

if [[ "$entity" == *.* ]]; then
  type_name=$entity
else
  type_name="switch.$entity"
fi

IFS='.' read -r type name <<< "$type_name"

if [[ -z "$to_state" ]]; then
  if ! state=$(/home/pi/scripts/tuya_status.sh "$name"); then
    write_log "could not read $type_name; refusing to guess toggle direction"
    if return 2>/dev/null; then return 1; else exit 1; fi
  fi
  [[ "$state" == "on" ]] && to_state=off || to_state=on
fi

write_log "turning type name: $type_name, type: $type, to_state: $to_state"

if ! /usr/bin/curl -fsS --max-time 15 -X POST \
  -H "Authorization: Bearer $token" \
  -H "Content-Type: application/json" \
  -d "{\"entity_id\": \"$type_name\"}" \
  "http://vanpi.local:8123/api/services/$type/turn_$to_state" > /dev/null; then
  write_log "failed to turn $type_name $to_state"
  if return 2>/dev/null; then return 1; else exit 1; fi
fi

echo "$to_state"
