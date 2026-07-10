#!/bin/bash
# usage: ntfy_send.sh <title> <message> [priority] [tags]
. /home/pi/scripts/backup_conf.sh

title=${1:?usage: ntfy_send.sh <title> <message> [priority] [tags]}
msg=${2:?}
prio=${3:-default}
tags=${4:-floppy_disk}

curl -fsS -m 15 \
  -H "Title: $title" -H "Priority: $prio" -H "Tags: $tags" \
  -d "$msg" "$NTFY_URL" >/dev/null \
  || echo "ntfy_send failed ($NTFY_URL): $title — $msg"
