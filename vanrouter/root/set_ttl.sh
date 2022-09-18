#! /bin/sh

active_iface() {
  active_iface_scores | sort | tail -1 | cut -d ':' -f2-
}

active_iface_scores() {
  find /tmp/run/mwan3track/*/SCORE | while read file; do
    printf "%d:%s\n" "$(cat $file)" "$(basename $(dirname $file))"
  done
}

cur_iface=$(active_iface)
echo "iface: $cur_iface" >> /root/log
if [ "$cur_iface" = "clientwan" ]; then
  ttl_val=65
else
  ttl_val=64
fi
echo "setting iface '$cur_iface' ttl to $ttl_val"
iptables -t mangle -I POSTROUTING -o wlan1 -j TTL --ttl-set $ttl_val -v
