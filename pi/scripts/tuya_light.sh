#!/bin/bash
set -euo pipefail

action=${1:-}
entity=${2:-}
ha_url=${TUYA_HA_URL:-http://vanpi.local:8123}
token_file=${TUYA_TOKEN_FILE:-/home/pi/secrets/localtuya_token}

if [[ "$action" != "list" && "$action" != "status" && "$action" != "set" && \
      "$action" != "hue" && "$action" != "temperature" ]]; then
  echo "usage: tuya_light.sh list | status <light.entity> | set <light.entity> <brightness> [color_temp_kelvin] | hue <light.entity> <degrees> | temperature <light.entity> <kelvin>" >&2
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
        brightness: .attributes.brightness,
        color_mode: (.attributes.color_mode // null),
        supported_color_modes: (.attributes.supported_color_modes // []),
        hs_color: (.attributes.hs_color // null),
        color_temp_kelvin: (.attributes.color_temp_kelvin // null),
        min_color_temp_kelvin: (.attributes.min_color_temp_kelvin // null),
        max_color_temp_kelvin: (.attributes.max_color_temp_kelvin // null)
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
    brightness: .attributes.brightness,
    color_mode: (.attributes.color_mode // null),
    supported_color_modes: (.attributes.supported_color_modes // []),
    hs_color: (.attributes.hs_color // null),
    color_temp_kelvin: (.attributes.color_temp_kelvin // null),
    min_color_temp_kelvin: (.attributes.min_color_temp_kelvin // null),
    max_color_temp_kelvin: (.attributes.max_color_temp_kelvin // null)
  }'
  exit
fi

action_value=${3:-}
color_temp_kelvin=${4:-}

if [[ "$action" == "hue" ]]; then
  if [[ ! "$action_value" =~ ^[0-9]+$ ]] || (( action_value < 0 || action_value > 360 )); then
    echo "hue must be from 0 to 360" >&2
    exit 2
  fi
  payload=$(/usr/bin/jq -cn \
    --arg entity_id "$entity" \
    --argjson hue "$action_value" \
    '{entity_id: $entity_id, hs_color: [$hue, 100]}')
elif [[ "$action" == "temperature" ]]; then
  if [[ ! "$action_value" =~ ^[0-9]+$ ]] || (( action_value < 2000 || action_value > 7000 )); then
    echo "color temperature must be from 2000 to 7000 kelvin" >&2
    exit 2
  fi
  payload=$(/usr/bin/jq -cn \
    --arg entity_id "$entity" \
    --argjson color_temp_kelvin "$action_value" \
    '{entity_id: $entity_id, color_temp_kelvin: $color_temp_kelvin}')
elif [[ ! "$action_value" =~ ^[0-9]+$ ]] || (( action_value < 1 || action_value > 255 )); then
  echo "brightness must be from 1 to 255" >&2
  exit 2
elif [[ -n "$color_temp_kelvin" && ( ! "$color_temp_kelvin" =~ ^[0-9]+$ || \
   color_temp_kelvin -lt 2000 || color_temp_kelvin -gt 7000 ) ]]; then
  echo "color_temp_kelvin must be from 2000 to 7000" >&2
  exit 2
elif [[ -n "$color_temp_kelvin" ]]; then
  payload=$(/usr/bin/jq -cn \
    --arg entity_id "$entity" \
    --argjson brightness "$action_value" \
    --argjson color_temp_kelvin "$color_temp_kelvin" \
    '{entity_id: $entity_id, brightness: $brightness, color_temp_kelvin: $color_temp_kelvin}')
else
  payload=$(/usr/bin/jq -cn \
    --arg entity_id "$entity" \
    --argjson brightness "$action_value" \
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

if [[ "$action" == "hue" ]]; then
  printf '{"hue":%s}\n' "$action_value"
elif [[ "$action" == "temperature" ]]; then
  printf '{"color_temp_kelvin":%s}\n' "$action_value"
elif [[ -n "$color_temp_kelvin" ]]; then
  printf '{"brightness":%s,"color_temp_kelvin":%s}\n' "$action_value" "$color_temp_kelvin"
else
  printf '{"brightness":%s}\n' "$action_value"
fi
