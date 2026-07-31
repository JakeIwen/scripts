#!/bin/zsh

# Report which dendelion AP currently has m4mac associated.
set -euo pipefail

if (( $# )); then
  print -u2 -- "Usage: ${0:t}"
  exit 2
fi

ssh_bin=${RADFREQ_SSH_BIN:-/usr/bin/ssh}
router_target=${RADFREQ_TARGET:-root@192.168.6.1}
client_name=${RADFREQ_CLIENT:-m4mac}

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
  /bin/sh -s -- "$client_name" <<'OPENWRT'
set -eu

client_name=$1
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

if /usr/sbin/iw dev wl1-ap0 station get "$client_mac" >/dev/null 2>&1; then
  printf '5 GHz — radio1\n'
elif /usr/sbin/iw dev wl0-ap0 station get "$client_mac" >/dev/null 2>&1; then
  printf '2.4 GHz — radio0\n'
else
  printf '%s is not associated with either dendelion AP\n' "$client_name" >&2
  exit 1
fi
OPENWRT
