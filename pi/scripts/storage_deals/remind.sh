#!/bin/bash
# Daily ntfy reminder of the storage purchases worth making during the 2026
# Black Friday sales. Cron runs it every day; it sends only inside the window,
# so it goes quiet on its own afterwards (remove the cron line after Dec 1).
#   remind.sh          send if today is inside the window
#   remind.sh --now    send regardless (to preview the message)
set -u

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
notify=${STORAGE_DEALS_NOTIFY:-$script_dir/../ntfy_send.sh}
message_file=${STORAGE_DEALS_MESSAGE:-$script_dir/message.txt}
today=${STORAGE_DEALS_TODAY:-$(/bin/date +%F)}

# Early Black Friday sales usually open the week before; Cyber Monday is Nov 30.
window_start=2026-11-20
window_end=2026-12-01
black_friday=2026-11-27
cyber_monday=2026-11-30

if [[ ${1:-} != --now ]] && [[ $today < $window_start || $today > $window_end ]]; then
  exit 0
fi

if [[ $today == "$black_friday" ]]; then
  when="Black Friday is today"
elif [[ $today == "$cyber_monday" ]]; then
  when="Cyber Monday is today"
elif [[ $today > $window_start || $today == "$window_start" ]] && [[ $today < $black_friday ]]; then
  days=$(( 10#${black_friday:8:2} - 10#${today:8:2} ))
  when="$days day$( (( days == 1 )) || echo s) to Black Friday"
else
  when="Cyber Week"
fi

[[ -s $message_file ]] || { echo "storage deals message is missing: $message_file" >&2; exit 1; }
NTFY_TOPIC_VAR=NTFY_PRICE_URL "$notify" "Storage deals: $when" "$(cat "$message_file")" \
  default shopping_cart
