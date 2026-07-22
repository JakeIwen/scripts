#!/usr/bin/env bash
set -o pipefail

if [ "$#" -ne 1 ] || [ -z "$1" ]; then
  echo "usage: parse_cron.sh '<minute> <hour> <day> <month> <weekday>'" >&2
  exit 2
fi

cron_expression=$1

for dependency in curl jq; do
  command -v "$dependency" >/dev/null 2>&1 || {
    echo "cron parser requires $dependency" >&2
    exit 1
  }
done

raw_response=$(
  curl --silent --show-error \
    --connect-timeout 5 --max-time 12 \
    --write-out $'\n%{http_code}' \
    https://www.uptimia.com/cron-expression-generator-action \
    --data-urlencode "expression=$cron_expression"
) || exit 1
http_status=${raw_response##*$'\n'}
response=${raw_response%$'\n'*}

if [ "$http_status" = 429 ]; then
  echo "cron parser rate limited" >&2
  exit 75
fi
if [[ "$http_status" != 2* ]]; then
  echo "cron parser HTTP error $http_status" >&2
  exit 1
fi

if printf '%s' "$response" | jq -e '.limited == true' >/dev/null 2>&1; then
  echo "cron parser rate limited" >&2
  exit 75
fi

description=$(printf '%s' "$response" | jq -er \
  '.text | select(type == "string" and length > 0)') || {
  echo "cron parser returned no description" >&2
  exit 1
}

printf '%s\n' "$description"
