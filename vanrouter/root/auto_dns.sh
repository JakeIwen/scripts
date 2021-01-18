#!/bin/sh

# The script automatically switches the DNS servers between Pi-hole and Cloudflare based on Pi-hole DNS Server status.

TARGET=192.168.6.4 # Pi-hole
FALLBACK_A=1.1.1.1 # Cloudflare
FALLBACK_B=1.0.0.1 # Cloudflare

function set_fallback_dns() {
    echo $(date)
    echo "Setting fallback DNS servers"
    echo $FALLBACK_A
    echo $FALLBACK_B

    uci -q delete dhcp.@dnsmasq[0].server
    uci add_list dhcp.@dnsmasq[0].server=$FALLBACK_A
    uci add_list dhcp.@dnsmasq[0].server=$FALLBACK_B
    uci commit dhcp
    /etc/init.d/dnsmasq restart
}

TARGET_PING_COUNT=$(ping -c 3 -w 3 $TARGET | grep seq | wc -l)
# check if pi is down
if [ $TARGET_PING_COUNT -eq 0 ]; then
    FALLBACK_DNS_COUNT=$(uci show dhcp.@dnsmasq[0].server | grep $FALLBACK_A | wc -l)
    # check if fallback is not set as a DNS server
    if [ $FALLBACK_DNS_COUNT -eq 0 ]; then
        set_fallback_dns
    fi
else
    TARGET_DNS_COUNT=$(uci show dhcp.@dnsmasq[0].server | grep $TARGET | wc -l)
    # check if target is not set as a DNS server
    if [ $TARGET_DNS_COUNT -eq 0 ]; then
        # check if raspi is up
        if ping -c 1 '192.168.6.4'; then
            echo $(date)
            echo "Setting target DNS server"
            echo $TARGET
            uci -q delete dhcp.@dnsmasq[0].server
            uci add_list dhcp.@dnsmasq[0].server=$TARGET
            uci commit dhcp
            /etc/init.d/dnsmasq restart
        else
            set_fallback_dns
        fi
    fi
fi