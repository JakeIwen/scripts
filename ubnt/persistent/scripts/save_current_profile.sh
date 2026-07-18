#!/bin/sh

manager=/etc/persistent/scripts/wifi_manager.sh
profile_name=${1:-$(iwgetid ath0 -r 2>/dev/null || iwgetid -r 2>/dev/null)}

[ -n "$profile_name" ] || {
    echo 'No associated SSID; provide a profile name.' >&2
    exit 1
}

exec "$manager" save-current "$profile_name"
