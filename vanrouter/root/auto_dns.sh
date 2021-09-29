#!/bin/sh

# The script automatically switches the DNS servers between Pi-hole and Cloudflare based on Pi-hole DNS Server status.

logfile="/root/log/auto_dns.log"
PIHOLE=192.168.6.103 # Pi-hole
FALLBACK_A=1.1.1.1 # Cloudflare
FALLBACK_B=1.0.0.1 # Cloudflare

DNS_CFG=$(uci show dhcp.@dnsmasq[0].server)
PIHOLE_PING_CT=$(ping -c 1 -w 1 $PIHOLE | grep seq | wc -l)
PIHOLE_DNS_CT=$(echo $DNS_CFG | grep $PIHOLE | wc -l)
FALLBACK_DNS_CT=$(echo $DNS_CFG | grep $FALLBACK_A | wc -l)

# delete_nth_from_last_line() {
#   lines=$(cat "$1" | wc -l)
#   target="$(( lines-$2 ))"
#   sed -i "${target}d" "$1"
# }

delete_last_line() {
  lines=$(cat "$1" | wc -l)
  sed -i "${lines}d" "$1"
}

echo_dns_data() {
  echo "$(date)"
  echo "DNS_CFG $DNS_CFG"
  echo "PIHOLE_PING_CT $PIHOLE_PING_CT"
  echo "PIHOLE_DNS_CT $PIHOLE_DNS_CT"
  echo "FALLBACK_DNS_CT $FALLBACK_DNS_CT"
}

set_dns() {
  echo "setting new dns"
  echo_dns_data
  echo ""
  uci -q delete dhcp.@dnsmasq[0].server
  uci add_list dhcp.@dnsmasq[0].server=$1
  if [ "$#" -eq 2 ]; then uci add_list dhcp.@dnsmasq[0].server=$2; fi
  uci commit dhcp
  /etc/init.d/dnsmasq restart
}

if [ $PIHOLE_PING_CT -eq 1 ] && [ $PIHOLE_DNS_CT -eq 0 ]; then
  echo "Setting target DNS server to pi: $PIHOLE"
  set_dns $PIHOLE
elif [ $PIHOLE_PING_CT -eq 0 ] && [ $FALLBACK_DNS_CT -eq 0 ]; then # && ping -c 1 $FALLBACK_A > /dev/null
  echo "Setting fallback DNS servers $FALLBACK_A $FALLBACK_B"
  set_dns $FALLBACK_A $FALLBACK_B
else
  if [ "$(cat $logfile | wc -l)" -gt 5 ]; then delete_last_line "$logfile"; fi
  echo "$(date) unchanged"
fi
