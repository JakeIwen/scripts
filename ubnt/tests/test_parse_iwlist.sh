#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
parser="$script_dir/../persistent/scripts/parse-iwlist.awk"
fixture="$script_dir/fixtures/iwlist-scan.txt"
actual=$(mktemp "${TMPDIR:-/tmp}/ubnt-parse-actual.XXXXXX")
expected=$(mktemp "${TMPDIR:-/tmp}/ubnt-parse-expected.XXXXXX")
trap 'rm -f "$actual" "$expected"' EXIT HUP INT TERM

awk -f "$parser" "$fixture" > "$actual"
printf '%s\n' \
    '92|denlink|wpa|2462|11|4E:EA:85:26:34:F4|-13' \
    '21|dendelion|wpa|2412|1|D8:EC:5E:8D:6A:3A|-55' \
    '60|A Network With Spaces|none|2437|6|00:11:22:33:44:55|-40' > "$expected"

diff -u "$expected" "$actual"
printf 'parse-iwlist: ok\n'
