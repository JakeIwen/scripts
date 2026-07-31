#!/bin/zsh

# Temporarily prevent m4mac from using dendelion's 2.4 GHz AP so macOS can
# roam to the same SSID on 5 GHz. The hostapd ban expires automatically.
set -euo pipefail

usage() {
  print -u2 -- "Usage: ${0:t} [seconds]"
  print -u2 -- "  seconds: integer from 1 through 3600 (default: 10)"
  exit 2
}

(( $# <= 1 )) || usage
seconds=${1:-10}
[[ "$seconds" == <-> ]] || usage
(( seconds >= 1 && seconds <= 3600 )) || usage

ssh_bin=${PAUSE_24G_SSH_BIN:-/usr/bin/ssh}
router_target=${PAUSE_24G_TARGET:-root@192.168.6.1}
client_name=${PAUSE_24G_CLIENT:-m4mac}
ban_ms=$(( seconds * 1000 ))

[[ -x "$ssh_bin" ]] || {
  print -u2 -- "SSH command is unavailable: $ssh_bin"
  exit 1
}
print -r -- "$client_name" \
  | /usr/bin/grep -Eq '^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$' || {
  print -u2 -- "Unsafe DHCP client name: $client_name"
  exit 1
}

"$ssh_bin" \
  -o BatchMode=yes \
  -o ConnectTimeout=5 \
  "$router_target" \
  /bin/sh -s -- "$ban_ms" "$client_name" <<'OPENWRT'
set -eu

ban_ms=$1
client_name=$2

case $ban_ms in
  ''|*[!0-9]*)
    printf 'Invalid ban duration received by OpenWrt\n' >&2
    exit 2
    ;;
esac

set -- $(/usr/bin/awk -v target="$client_name" '$4 == target {print $2}' /tmp/dhcp.leases)
[ "$#" -eq 1 ] || {
  printf 'Expected exactly one %s DHCP lease; found %s\n' "$client_name" "$#" >&2
  exit 1
}

client_mac=$1
printf '%s\n' "$client_mac" \
  | /bin/grep -Eq '^([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}$' \
  || {
    printf 'OpenWrt returned an invalid MAC for %s\n' "$client_name" >&2
    exit 1
  }

if ! /usr/sbin/iw dev wl0-ap0 station get "$client_mac" >/dev/null 2>&1; then
  if /usr/sbin/iw dev wl1-ap0 station get "$client_mac" >/dev/null 2>&1; then
    printf '%s is already associated with the 5 GHz AP\n' "$client_name"
    exit 0
  fi
  printf '%s is not currently associated with either dendelion AP\n' \
    "$client_name" >&2
  exit 1
fi

/bin/ubus call hostapd.wl0-ap0 del_client \
  "{\"addr\":\"$client_mac\",\"reason\":5,\"deauth\":true,\"ban_time\":$ban_ms}" \
  >/dev/null
printf 'Paused 2.4 GHz for %s for %s ms; macOS should roam to 5 GHz\n' \
  "$client_name" "$ban_ms"
OPENWRT
