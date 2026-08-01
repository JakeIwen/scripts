#!/bin/bash
set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
installer="$repo_root/pi/scripts/configure_rtl9201_uas_quirk.sh"
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

run_installer() {
  RTL9201_UAS_CMDLINE_PATH="$test_root/cmdline.txt" \
  RTL9201_UAS_BACKUP_PATH="$test_root/cmdline.backup" \
  RTL9201_UAS_SYNC=/usr/bin/true \
  bash "$installer"
}

base='console=tty1 root=PARTUUID=1234-02 rootwait'
printf '%s\n\n' "$base" > "$test_root/cmdline.txt"
run_installer >/dev/null || fail "could not append the RTL9201 quirk"
grep -Eq ' usb-storage\.quirks=0bda:9201:u$' "$test_root/cmdline.txt" ||
  fail "new RTL9201 quirk was not appended"
[[ $(< "$test_root/cmdline.backup") == "$base" ]] ||
  fail "one-time kernel-command-line backup is wrong"

first_result=$(< "$test_root/cmdline.txt")
run_installer >/dev/null || fail "idempotent quirk install failed"
[[ $(< "$test_root/cmdline.txt") == "$first_result" ]] ||
  fail "idempotent quirk install changed the command line"

printf '%s\n' "$base usb-storage.quirks=abcd:1234:u,0bda:9201:g" \
  > "$test_root/cmdline.txt"
run_installer >/dev/null || fail "could not augment existing RTL9201 flags"
grep -Eq 'usb-storage\.quirks=abcd:1234:u,0bda:9201:gu($| )' \
  "$test_root/cmdline.txt" ||
  fail "existing quirk entries or flags were not preserved"

unsafe="$base usb-storage.quirks=abcd:1234:u usb-storage.quirks=0bda:9201:g"
printf '%s\n' "$unsafe" > "$test_root/cmdline.txt"
run_installer >/dev/null 2>&1 &&
  fail "multiple usb-storage.quirks parameters were accepted"
[[ $(< "$test_root/cmdline.txt") == "$unsafe" ]] ||
  fail "refused kernel command line was modified"

printf '%s\n%s\n' "$base" 'console=tty2' > "$test_root/cmdline.txt"
run_installer >/dev/null 2>&1 &&
  fail "multiple non-empty kernel command lines were accepted"

echo "PASS: RTL9201 IGNORE_UAS boot quirk installation is guarded and idempotent"
