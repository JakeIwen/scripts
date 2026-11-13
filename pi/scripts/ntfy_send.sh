#!/bin/bash
# generic ntfy notifier, usable from any script
#   usage: ntfy_send.sh <title> <message> [priority] [tags]
# URL resolution: $NTFY_URL > named $NTFY_TOPIC_VAR > $NTFY_MESSAGE_URL > local
# NTFY_SECRETS_FILE replaces the settings file (tests point it at a scratch
# file, so the production topic stays out of reach even on the Pi); unset,
# the Pi's own file is read as before.
ntfy_secrets=${NTFY_SECRETS_FILE:-/home/pi/secrets/.bash_variables}
[ -f "$ntfy_secrets" ] && . "$ntfy_secrets"
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

# Header values are one line: a CR or LF in a title (session titles and reply
# lines reach here) would end the header and start another one.
one_line() { local value=${1//$'\r'/ }; printf '%s' "${value//$'\n'/ }"; }
title=$(one_line "$title")
prio=$(one_line "$prio")
tags=$(one_line "$tags")

args=(-H "Title: $title" -H "Priority: $prio")
[ -n "$tags" ] && args+=(-H "Tags: $tags")

# --data-raw, not -d: -d reads a message starting with @ as a file to post.
if ! /usr/bin/curl -fsS --connect-timeout 5 --max-time 15 "${args[@]}" --data-raw "$msg" "$url" >/dev/null; then
  echo "ntfy_send failed: $title" >&2
  exit 1
fi
