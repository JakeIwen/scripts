#!/bin/bash
set -euo pipefail

action=${1:-}
entity=${2:-}
ha_url=${TUYA_HA_URL:-http://vanpi.local:8123}
token_file=${TUYA_TOKEN_FILE:-/home/pi/secrets/localtuya_token}

if [[ "$action" != "list" && "$action" != "status" && "$action" != "set" ]]; then
  echo "usage: tuya_light.sh list | <status|set> <light.entity> [brightness [color_temp_kelvin]]" >&2
  exit 2
fi
if [[ "$action" != "list" && ! "$entity" =~ ^light\.[a-z0-9_]+$ ]]; then
  echo "expected a light entity, got '$entity'" >&2
  exit 2
fi

token=$(/usr/bin/cat -- "$token_file")

if [[ "$action" == "list" ]]; then
  response=$(/usr/bin/curl -fsS --max-time 15 \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    "$ha_url/api/states") || {
      echo "failed to list lights" >&2
      exit 1
    }
  printf '%s' "$response" | /usr/bin/jq -ce '[
    .[]
    | select(
        (.entity_id | startswith("light."))
        or .entity_id == "switch.ext_flood"
        or .entity_id == "switch.solder_flood"
      )
    | {
        entity_id,
        state,
        brightness: .attributes.brightness
      }
  ]'
  exit
fi

if [[ "$action" == "status" ]]; then
  response=$(/usr/bin/curl -fsS --max-time 15 \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    "$ha_url/api/states/$entity") || {
      echo "failed to read $entity" >&2
      exit 1
    }
  printf '%s' "$response" | /usr/bin/jq -ce '{
    state,
    color_mode: .attributes.color_mode,
    brightness: .attributes.brightness,
    color_temp_kelvin: .attributes.color_temp_kelvin
  }'
  exit
fi

brightness=${3:-}
color_temp_kelvin=${4:-}
if [[ ! "$brightness" =~ ^[0-9]+$ ]] || (( brightness < 1 || brightness > 255 )); then
  echo "brightness must be from 1 to 255" >&2
  exit 2
fi
if [[ -n "$color_temp_kelvin" && ( ! "$color_temp_kelvin" =~ ^[0-9]+$ || \
   color_temp_kelvin -lt 2000 || color_temp_kelvin -gt 7000 ) ]]; then
  echo "color_temp_kelvin must be from 2000 to 7000" >&2
  exit 2
fi

if [[ -n "$color_temp_kelvin" ]]; then
  payload=$(/usr/bin/jq -cn \
    --arg entity_id "$entity" \
    --argjson brightness "$brightness" \
    --argjson color_temp_kelvin "$color_temp_kelvin" \
    '{entity_id: $entity_id, brightness: $brightness, color_temp_kelvin: $color_temp_kelvin}')
else
  payload=$(/usr/bin/jq -cn \
    --arg entity_id "$entity" \
    --argjson brightness "$brightness" \
    '{entity_id: $entity_id, brightness: $brightness}')
fi

if ! /usr/bin/curl -fsS --max-time 15 -X POST \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d "$payload" \
    "$ha_url/api/services/light/turn_on" >/dev/null; then
  echo "failed to set $entity" >&2
  exit 1
fi

if [[ -n "$color_temp_kelvin" ]]; then
  printf '{"brightness":%s,"color_temp_kelvin":%s}\n' "$brightness" "$color_temp_kelvin"
else
  printf '{"brightness":%s}\n' "$brightness"
fi
