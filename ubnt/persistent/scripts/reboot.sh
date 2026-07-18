#!/bin/sh

ccq=$(mca-status 2>/dev/null | awk -F= '$1 == "ccq" {print $2; exit}' | tr -d '\r')
case $ccq in
    ''|*[!0-9]*) ccq=0 ;;
esac

if [ "$ccq" -gt 300 ]; then
    echo 'link is up, not rebooting'
else
    reboot
fi
