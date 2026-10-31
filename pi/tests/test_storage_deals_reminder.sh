#!/bin/bash
set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
. "$repo_root/pi/tests/lib.sh"
remind="$repo_root/pi/scripts/storage_deals/remind.sh"
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

calls="$test_root/calls"
cat > "$test_root/notify" <<STUB
#!/bin/bash
printf '%s|%s|%s|%s|%s\n' "\$NTFY_TOPIC_VAR" "\$1" "\$3" "\$4" "\${#2}" >> "$calls"
STUB
chmod +x "$test_root/notify"
touch "$calls"

run_on() { # <YYYY-MM-DD> [args]
  local day=$1
  shift
  STORAGE_DEALS_NOTIFY="$test_root/notify" STORAGE_DEALS_TODAY="$day" bash "$remind" "$@"
}
title_on() { : > "$calls"; run_on "$@"; cut -d'|' -f2 "$calls"; }

assert_eq "" "$(title_on 2026-11-19)" "the day before the window is silent"
assert_eq "Storage deals: 7 days to Black Friday" "$(title_on 2026-11-20)" "the window opens on Nov 20"
assert_eq "Storage deals: 1 day to Black Friday" "$(title_on 2026-11-26)" "the countdown reaches one day"
assert_eq "Storage deals: Black Friday is today" "$(title_on 2026-11-27)" "Black Friday is named"
assert_eq "Storage deals: Cyber Week" "$(title_on 2026-11-28)" "the weekend is Cyber Week"
assert_eq "Storage deals: Cyber Monday is today" "$(title_on 2026-11-30)" "Cyber Monday is named"
assert_eq "Storage deals: Cyber Week" "$(title_on 2026-12-01)" "Dec 1 is the last day"
assert_eq "" "$(title_on 2026-12-02)" "the reminder goes quiet after the window"
assert_eq "" "$(title_on 2027-11-25)" "next year's November does not reopen the window"
assert_eq "Storage deals: Cyber Week" "$(title_on 2026-12-15 --now)" "--now previews outside the window"

: > "$calls"
run_on 2026-11-27
IFS='|' read -r topic _ priority tags length < "$calls"
assert_eq "NTFY_PRICE_URL default shopping_cart" "$topic $priority $tags" "it posts to the price topic"
(( length > 200 && length < 4096 )) || fail "message length $length is outside ntfy's limits"

echo "PASS: the storage-deals reminder sends daily Nov 20 to Dec 1, 2026 only"
