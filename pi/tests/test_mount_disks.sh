#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_eq() {
  local expected=$1 actual=$2 description=$3
  [[ "$actual" == "$expected" ]] ||
    fail "$description (expected '$expected', got '$actual')"
}

export MOUNT_DISKS_LIBRARY_ONLY=1
# shellcheck source=../scripts/mount_disks.sh
source "$repo_root/pi/scripts/mount_disks.sh"

# The production target is GNU/Linux; macOS readlink lacks GNU's -f option.
md_canonical_path() {
  printf '%s\n' "$1"
}
md_first_mount_dir_entry() {
  /usr/bin/find "$1" -mindepth 1 -maxdepth 1 -print -quit
}

# Simulate a mountinfo entry for a vanished old enumeration.  The expected
# device deliberately differs from the stale source, as it did when movingparts
# reappeared under a new /dev/sdX name.
md_find_exact_mount_source() {
  MD_MOUNT_SOURCE=/dev/vanished-test-device
  return 0
}
output=$(md_check_existing_mount movingparts /mnt/movingparts /dev/current-test-device 2>&1)
status=$?
assert_eq 2 "$status" "stale mount must fail closed"
[[ "$output" == *"stale mount"* && "$output" == *"vanished source"* ]] ||
  fail "stale mount diagnostic is not explicit: $output"

md_find_exact_mount_source() {
  MD_MOUNT_SOURCE=/dev/current-test-device
  return 0
}
md_check_existing_mount movingparts /mnt/movingparts /dev/current-test-device >/dev/null 2>&1
assert_eq 0 "$?" "intended device at exact target must be accepted"

md_find_exact_mount_source() {
  MD_MOUNT_SOURCE=""
  return 1
}
md_check_existing_mount movingparts /mnt/movingparts /dev/current-test-device >/dev/null 2>&1
assert_eq 1 "$?" "an unmounted target must continue to underlay validation"

mkdir "$test_root/mount-target"
md_require_empty_mount_dir "$test_root/mount-target" >/dev/null 2>&1 ||
  fail "empty underlying mount directory was rejected"
touch "$test_root/mount-target/.hidden-underlay-data"
if md_require_empty_mount_dir "$test_root/mount-target" >/dev/null 2>&1; then
  fail "nonempty underlying mount directory was accepted"
fi
[[ -e "$test_root/mount-target/.hidden-underlay-data" ]] ||
  fail "underlying data was modified"

md_first_mount_dir_entry() {
  echo "find: failed to restore inaccessible working directory" >&2
  return 1
}
output=$(md_require_empty_mount_dir "$test_root/mount-target" 2>&1)
status=$?
assert_eq 1 "$status" "directory probe failure must fail closed"
[[ "$output" == *"directory probe diagnostic:"* &&
   "$output" == *"failed to restore inaccessible working directory"* ]] ||
  fail "directory probe stderr was not preserved: $output"

# A failure for the first label must survive later successful/missing labels.
calls=()
rm_mnt_dir() { return 0; }
mntdsk() {
  calls+=("$1")
  [[ "$1" != movingparts ]]
}
md_fix_hfs_mounts() { return 0; }
md_print_mounts() { return 0; }
mount_disks_main >/dev/null 2>&1
status=$?
assert_eq 1 "$status" "multi-disk reconciliation must propagate one label failure"
assert_eq 6 "${#calls[@]}" "multi-disk reconciliation must still check every label"

calls=()
mount_disks_main movingparts >/dev/null 2>&1
status=$?
assert_eq 1 "$status" "single-label mount failure must propagate"
assert_eq 1 "${#calls[@]}" "single-label reconciliation call count"

mount_disks_main one two >/dev/null 2>&1
assert_eq 2 "$?" "invalid argument count must return usage failure"

echo "PASS: mount disk stale-source, underlay, and status safeguards"
