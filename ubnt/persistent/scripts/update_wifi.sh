#!/bin/sh

# Compatibility entry point for older callers.
manager=/etc/persistent/scripts/wifi_manager.sh

if [ "$#" -eq 1 ]; then
    exec "$manager" connect "$1"
fi

exec "$manager" auto
