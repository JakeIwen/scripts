#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
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
# shellcheck source=../../scripts/mount_disks.sh
source "$repo_root/pi/scripts/mount_disks.sh"

DISK_HEALTH_STATE_DIR="$test_root/disk-health"
export DISK_HEALTH_STATE_DIR
mkdir -p "$DISK_HEALTH_STATE_DIR/quarantine"
touch "$DISK_HEALTH_STATE_DIR/quarantine/movingparts"
output=$(mntdsk movingparts 2>&1)
status=$?
assert_eq 1 "$status" "quarantined filesystem was accepted for mounting"
[[ "$output" == *"quarantined after a failed filesystem check"* ]] ||
  fail "quarantine refusal was not actionable"
rm "$DISK_HEALTH_STATE_DIR/quarantine/movingparts"
grep -Fq 'fmask=0022,dmask=0022' "$repo_root/pi/scripts/mount_disks.sh" ||
  fail "exFAT mount does not grant the pi owner usable permissions"

fake_samba_control="$test_root/samba-share-control"
fake_samba_calls="$test_root/samba-calls"
cat > "$fake_samba_control" <<EOF
#!/bin/bash
printf '%s\\n' "\$*" >> "$fake_samba_calls"
EOF
chmod +x "$fake_samba_control"
md_samba_share_control="$fake_samba_control"

md_clear_samba_drain movingparts ||
  fail "mounted Samba disk could not clear its reconnect drain"
assert_eq "clear movingparts" "$(cat "$fake_samba_calls")" \
  "Samba drain clear did not use the exact filesystem label"
: > "$fake_samba_calls"
md_clear_samba_drain hfs2tb ||
  fail "non-Samba disk was treated as a drain-clear failure"
[[ ! -s "$fake_samba_calls" ]] ||
  fail "non-Samba disk invoked Samba drain control"

# The production target is GNU/Linux; macOS readlink lacks GNU's -f option.
md_canonical_path() {
  printf '%s\n' "$1"
}
md_first_blocking_mount_dir_entry() {
  /usr/bin/find "$1" -mindepth 1 -maxdepth 1 \
    ! \( -type f \( -name .DS_Store -o -name '._.DS_Store' \) \) \
    -print -quit
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

mkdir "$test_root/finder-metadata-only"
touch "$test_root/finder-metadata-only/.DS_Store" \
  "$test_root/finder-metadata-only/._.DS_Store"
md_require_empty_mount_dir "$test_root/finder-metadata-only" >/dev/null 2>&1 ||
  fail "regular Finder metadata files blocked the mount target"
[[ -f "$test_root/finder-metadata-only/.DS_Store" &&
   -f "$test_root/finder-metadata-only/._.DS_Store" ]] ||
  fail "Finder metadata files were modified"

mkdir -p "$test_root/finder-metadata-directory/.DS_Store"
if md_require_empty_mount_dir "$test_root/finder-metadata-directory" >/dev/null 2>&1; then
  fail "a directory named .DS_Store was accepted as Finder metadata"
fi

mkdir "$test_root/finder-metadata-symlink"
ln -s /dev/null "$test_root/finder-metadata-symlink/.DS_Store"
if md_require_empty_mount_dir "$test_root/finder-metadata-symlink" >/dev/null 2>&1; then
  fail "a symlink named .DS_Store was accepted as Finder metadata"
fi

mkdir "$test_root/similarly-named-metadata"
touch "$test_root/similarly-named-metadata/not-really.DS_Store"
if md_require_empty_mount_dir "$test_root/similarly-named-metadata" >/dev/null 2>&1; then
  fail "a similarly named file was accepted as standard Finder metadata"
fi

touch "$test_root/mount-target/.hidden-underlay-data"
if md_require_empty_mount_dir "$test_root/mount-target" >/dev/null 2>&1; then
  fail "nonempty underlying mount directory was accepted"
fi
[[ -e "$test_root/mount-target/.hidden-underlay-data" ]] ||
  fail "underlying data was modified"

md_first_blocking_mount_dir_entry() {
  echo "find: failed to restore inaccessible working directory" >&2
  return 1
}
output=$(md_require_empty_mount_dir "$test_root/mount-target" 2>&1)
status=$?
assert_eq 1 "$status" "directory probe failure must fail closed"
[[ "$output" == *"directory probe diagnostic:"* &&
   "$output" == *"failed to restore inaccessible working directory"* ]] ||
  fail "directory probe stderr was not preserved: $output"

# Only a vanished /dev source at an allowlisted target is stale. A live source
# must never be selected for automatic recovery.
MOUNT_LABELS=(movingparts EXFAT512)
TEST_MOVING_SOURCE_FILE="$test_root/moving-source"
TEST_EXFAT_SOURCE_FILE="$test_root/exfat-source"
TEST_UNMOUNT_CALLS_FILE="$test_root/unmount-calls"
printf '%s\n' /dev/vanished-test-device > "$TEST_MOVING_SOURCE_FILE"
printf '%s\n' /dev/live-test-device > "$TEST_EXFAT_SOURCE_FILE"
: > "$TEST_UNMOUNT_CALLS_FILE"
md_find_exact_mount_source() {
  case "$1" in
    /mnt/movingparts) IFS= read -r MD_MOUNT_SOURCE < "$TEST_MOVING_SOURCE_FILE" ;;
    /mnt/EXFAT512) IFS= read -r MD_MOUNT_SOURCE < "$TEST_EXFAT_SOURCE_FILE" ;;
    *) MD_MOUNT_SOURCE=""; return 1 ;;
  esac
  [[ -n "$MD_MOUNT_SOURCE" ]] || return 1
}
md_mount_source_is_live() {
  [[ "$1" == /dev/live-test-device ]]
}
md_normal_unmount_stale_target() {
  printf '%s\n' "$1" >> "$TEST_UNMOUNT_CALLS_FILE"
  case "$1" in
    /mnt/movingparts) : > "$TEST_MOVING_SOURCE_FILE" ;;
    /mnt/EXFAT512) : > "$TEST_EXFAT_SOURCE_FILE" ;;
    *) return 1 ;;
  esac
}

output=$(md_list_stale_mounts 2>&1) || fail "stale mount scan returned failure"
[[ "$output" == $'movingparts\t/mnt/movingparts\t/dev/vanished-test-device' ]] ||
  fail "stale scan did not isolate the vanished source: $output"

md_recover_stale_mounts >/dev/null 2>&1 || fail "stale recovery returned failure"
assert_eq "/mnt/movingparts" "$(cat "$TEST_UNMOUNT_CALLS_FILE")" \
  "stale recovery must normally unmount only the vanished source"
assert_eq "" "$(cat "$TEST_MOVING_SOURCE_FILE")" "stale recovery did not clear the stale target"
assert_eq "/dev/live-test-device" "$(cat "$TEST_EXFAT_SOURCE_FILE")" \
  "stale recovery modified a live mount"

printf '%s\n' /dev/vanished-test-device > "$TEST_MOVING_SOURCE_FILE"
md_normal_unmount_stale_target() { return 32; }
if md_recover_stale_mounts >/dev/null 2>&1; then
  fail "failed normal stale unmount was reported successful"
fi
assert_eq "/dev/vanished-test-device" "$(cat "$TEST_MOVING_SOURCE_FILE")" \
  "failed normal stale unmount changed target state"

MOUNT_LABELS=(movingparts mbp1tbkup mbp2tbkup hfs2tb usbext EXFAT512)

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

# A dashboard eject hold must skip only that label during automatic (no-arg)
# reconciliation. An explicit manual mount remains available after diskctl
# clears the hold.
DISK_EJECT_HOLD_DIR="$test_root/eject-holds"
DISK_EJECT_NOW=4000000
disk_eject_hold_set mbp2tbkup 60 >/dev/null || fail "could not create eject hold"
calls=()
mntdsk() {
  calls+=("$1")
  return 0
}
mount_disks_main >/dev/null 2>&1 || fail "eject hold made reconciliation fail"
assert_eq 5 "${#calls[@]}" "automatic reconciliation did not skip exactly one held label"
[[ " ${calls[*]} " != *" mbp2tbkup "* ]] || fail "held label was automatically mounted"
calls=()
mount_disks_main mbp2tbkup >/dev/null 2>&1 || fail "explicit mount was blocked by eject hold"
assert_eq "mbp2tbkup" "${calls[*]}" "explicit mount did not target held label"
disk_eject_hold_clear mbp2tbkup || fail "could not clear eject hold"

ALWAYS_MOUNT_LABELS=(EXFAT512)
disk_eject_hold_set EXFAT512 60 >/dev/null || fail "could not hold always-mounted label"
calls=()
mount_disks_main --always >/dev/null 2>&1 || fail "always-only held reconciliation failed"
assert_eq 0 "${#calls[@]}" "always-only reconciliation ignored eject hold"
disk_eject_hold_clear EXFAT512 || fail "could not clear always-mounted eject hold"
mount_disks_main --always >/dev/null 2>&1 || fail "always-only reconciliation failed"
assert_eq "EXFAT512" "${calls[*]}" "always-only reconciliation targeted another label"

calls=()
mntdsk() {
  calls+=("$1")
  [[ "$1" != movingparts ]]
}
mount_disks_main movingparts >/dev/null 2>&1
status=$?
assert_eq 1 "$status" "single-label mount failure must propagate"
assert_eq 1 "${#calls[@]}" "single-label reconciliation call count"

mount_disks_main one two >/dev/null 2>&1
assert_eq 2 "$?" "invalid argument count must return usage failure"
mount_disks_main --unknown-mode >/dev/null 2>&1
assert_eq 2 "$?" "unknown maintenance mode must return usage failure"

echo "PASS: mount disk stale-source recovery, underlay, and status safeguards"
