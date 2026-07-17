#!/bin/bash
# Rate-limited ignition HDD shutdown alerts.
#
# The active marker is intentionally kept under /run so notification state is
# cleared by a reboot.  A delivered failure is repeated after 30 minutes only
# if the exact failure summary is still occurring.  The next successful HDD
# shutdown sends one recovery notification and clears the marker.

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HA_STATE_DIR=${HDD_ALERT_STATE_DIR:-/run/lock/vanpi-hdd-alerts}
HA_ACTIVE_FILE="$HA_STATE_DIR/active"
HA_COOLDOWN_SECONDS=${HDD_ALERT_COOLDOWN_SECONDS:-1800}

ha_usage() {
  echo "usage: ${0##*/} failure <summary>" >&2
  echo "       ${0##*/} recovery" >&2
}

ha_load_url() {
  if [[ -z "${NTFY_MESSAGE_URL:-}" && -f /home/pi/secrets/.bash_variables ]]; then
    # shellcheck disable=SC1091
    . /home/pi/secrets/.bash_variables
  fi
  if [[ -z "${NTFY_MESSAGE_URL:-}" ]]; then
    echo "ERROR: NTFY_MESSAGE_URL is not configured; cannot send HDD alert" >&2
    return 1
  fi
}

ha_prepare_state_dir() {
  if [[ -L "$HA_STATE_DIR" || ( -e "$HA_STATE_DIR" && ! -d "$HA_STATE_DIR" ) ]]; then
    echo "ERROR: $HA_STATE_DIR is not a safe state directory" >&2
    return 1
  fi
  /usr/bin/install -d -m 700 -- "$HA_STATE_DIR"
}

ha_send() {
  local title=$1 message=$2 priority=$3 tags=$4
  ha_load_url || return 1
  NTFY_URL="$NTFY_MESSAGE_URL" "$script_dir/ntfy_send.sh" \
    "$title" "$message" "$priority" "$tags"
}

ha_write_active() {
  local fingerprint=$1 timestamp=$2 tmp
  ha_prepare_state_dir || return 1
  tmp="$HA_STATE_DIR/.active.$$"
  if ! (umask 077; printf '%s %s\n' "$fingerprint" "$timestamp" > "$tmp"); then
    echo "ERROR: cannot write HDD alert state" >&2
    return 1
  fi
  if ! /usr/bin/mv -f -- "$tmp" "$HA_ACTIVE_FILE"; then
    /usr/bin/rm -f -- "$tmp"
    echo "ERROR: cannot install HDD alert state" >&2
    return 1
  fi
}

ha_failure() {
  local message=$1 fingerprint_line fingerprint now previous_fingerprint previous_time age

  fingerprint_line=$(printf '%s' "$message" | /usr/bin/sha256sum) || return 1
  fingerprint=${fingerprint_line%% *}
  now=$(/usr/bin/date +%s) || return 1

  if [[ -f "$HA_ACTIVE_FILE" ]] &&
      IFS=' ' read -r previous_fingerprint previous_time < "$HA_ACTIVE_FILE" &&
      [[ "$previous_fingerprint" == "$fingerprint" && "$previous_time" =~ ^[0-9]+$ ]]; then
    age=$((now - previous_time))
    if (( age >= 0 && age < HA_COOLDOWN_SECONDS )); then
      echo "HDD shutdown alert unchanged; suppressing repeat for $((HA_COOLDOWN_SECONDS - age)) more seconds"
      return 0
    fi
  fi

  ha_send "vanpi HDD shutdown failed" "$message" high warning || return 1
  ha_write_active "$fingerprint" "$now"
}

ha_recovery() {
  [[ -f "$HA_ACTIVE_FILE" ]] || return 0
  ha_send \
    "vanpi HDD shutdown recovered" \
    "The allowlisted HDDs were unmounted and spun down successfully after a previous failure." \
    default white_check_mark || return 1
  /usr/bin/rm -f -- "$HA_ACTIVE_FILE"
}

if [[ ! "$HA_COOLDOWN_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: HDD_ALERT_COOLDOWN_SECONDS must be a positive integer" >&2
  exit 2
fi

case "${1:-}" in
  failure)
    [[ $# == 2 && -n "$2" ]] || { ha_usage; exit 2; }
    ha_failure "$2"
    ;;
  recovery)
    [[ $# == 1 ]] || { ha_usage; exit 2; }
    ha_recovery
    ;;
  *)
    ha_usage
    exit 2
    ;;
esac
