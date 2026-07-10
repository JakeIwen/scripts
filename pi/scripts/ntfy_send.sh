#!/bin/bash
# generic ntfy notifier, usable from any script
#   usage: ntfy_send.sh <title> <message> [priority] [tags]
# URL resolution: caller-exported $NTFY_URL > $NTFY_MESSAGE_URL (secrets) > local server
[ -f /home/pi/secrets/.bash_variables ] && . /home/pi/secrets/.bash_variables
url="${NTFY_URL:-${NTFY_MESSAGE_URL:-http://127.0.0.1/vanpi}}"

title=${1:?usage: ntfy_send.sh <title> <message> [priority] [tags]}
msg=${2:?}
prio=${3:-default}
tags=${4:-}

args=(-H "Title: $title" -H "Priority: $prio")
[ -n "$tags" ] && args+=(-H "Tags: $tags")

curl -fsS -m 15 "${args[@]}" -d "$msg" "$url" >/dev/null \
  || echo "ntfy_send failed ($url): $title — $msg"
