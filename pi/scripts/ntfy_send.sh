#!/bin/bash
# generic ntfy notifier, usable from any script
#   usage: ntfy_send.sh <title> <message> [priority] [tags]
# URL resolution: $NTFY_URL > named $NTFY_TOPIC_VAR > $NTFY_MESSAGE_URL > local
[ -f /home/pi/secrets/.bash_variables ] && . /home/pi/secrets/.bash_variables
topic_url=
if [ -n "${NTFY_TOPIC_VAR:-}" ]; then
  [[ "$NTFY_TOPIC_VAR" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || {
    echo "ntfy_send: invalid NTFY_TOPIC_VAR" >&2
    exit 2
  }
  topic_url=${!NTFY_TOPIC_VAR:-}
fi
url="${NTFY_URL:-${topic_url:-${NTFY_MESSAGE_URL:-http://127.0.0.1/vanpi}}}"

title=${1:?usage: ntfy_send.sh <title> <message> [priority] [tags]}
msg=${2:?}
prio=${3:-default}
tags=${4:-}

args=(-H "Title: $title" -H "Priority: $prio")
[ -n "$tags" ] && args+=(-H "Tags: $tags")

if ! /usr/bin/curl -fsS --connect-timeout 5 --max-time 15 "${args[@]}" -d "$msg" "$url" >/dev/null; then
  echo "ntfy_send failed: $title" >&2
  exit 1
fi
