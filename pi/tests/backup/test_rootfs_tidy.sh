#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

# shellcheck source=../lib.sh
source "$repo_root/pi/tests/lib.sh"
tidy="$repo_root/pi/scripts/rootfs_tidy.sh"
fake_bin="$test_root/bin"
mkdir -p "$fake_bin"

cat > "$fake_bin/flock" <<'EOF'
#!/bin/bash
exit "${TEST_LOCK_HELD:-0}"
EOF
cat > "$fake_bin/id" <<'EOF'
#!/bin/bash
printf '%s\n' 1000
EOF
cat > "$fake_bin/sudo" <<'EOF'
#!/bin/bash
exit 1
EOF
cat > "$fake_bin/findmnt" <<'EOF'
#!/bin/bash
(( ${TEST_FINDMNT_STATUS:-0} == 0 )) || exit "$TEST_FINDMNT_STATUS"
if (( ${TEST_FINDMNT_EMPTY:-0} == 0 )); then
  printf '%s\n' /
  [[ -n "${TEST_MOUNT_TARGET:-}" ]] && printf '%s\n' "$TEST_MOUNT_TARGET"
fi
exit 0
EOF
cat > "$fake_bin/device-stat" <<'EOF'
#!/bin/bash
for path in "$@"; do :; done
if [[ "$path" == "${TEST_DEVICE_MISMATCH_PATH:-}" ]]; then
  printf '%s\n' 200
else
  printf '%s\n' 100
fi
EOF
cat > "$fake_bin/rm" <<'EOF'
#!/bin/bash
one_file_system=0
target=
for argument in "$@"; do
  case "$argument" in
    --one-file-system) one_file_system=1 ;;
    -rf|-fr|--) ;;
    *) target=$argument ;;
  esac
done
(( one_file_system == 1 )) || exit 90
[[ -n "$target" ]] || exit 91
printf '%s\n' "$target" >> "$TEST_RM_LOG"
/bin/rm -rf -- "$target"
EOF
chmod +x "$fake_bin"/*

make_fixture() {
  local root=$1 standalone="$1/standalone" releases="$1/standalone/releases"
  mkdir -p "$releases/release-1" "$releases/release-2" \
    "$releases/release-3" "$releases/release-4"
  touch "$standalone/install.lock"
  touch -t 202601010101 "$releases/release-1"
  touch -t 202602010101 "$releases/release-2"
  touch -t 202603010101 "$releases/release-3"
  touch -t 202604010101 "$releases/release-4"
  ln -s "$releases/release-2" "$standalone/current"
}

run_tidy() {
  local root=$1
  shift
  ROOTFS_TIDY_CODEX_STANDALONE="$root/standalone" \
  ROOTFS_TIDY_FLOCK="$fake_bin/flock" \
  ROOTFS_TIDY_FINDMNT="$fake_bin/findmnt" \
  ROOTFS_TIDY_DEVICE_STAT="$fake_bin/device-stat" \
  ROOTFS_TIDY_RM="$fake_bin/rm" \
  ROOTFS_TIDY_ID="$fake_bin/id" \
  ROOTFS_TIDY_SUDO="$fake_bin/sudo" \
  TEST_LOCK_HELD="${TEST_LOCK_HELD:-0}" \
  TEST_FINDMNT_STATUS="${TEST_FINDMNT_STATUS:-0}" \
  TEST_FINDMNT_EMPTY="${TEST_FINDMNT_EMPTY:-0}" \
  TEST_MOUNT_TARGET="${TEST_MOUNT_TARGET:-}" \
  TEST_DEVICE_MISMATCH_PATH="${TEST_DEVICE_MISMATCH_PATH:-}" \
  TEST_RM_LOG="$test_root/rm.log" \
    bash "$tidy" "$@"
}

fixture="$test_root/dry-apply"
make_fixture "$fixture"
run_tidy "$fixture" > "$test_root/dry-output"
count=$(find "$fixture/standalone/releases" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
assert_eq 4 "$count" "tidy dry-run changed release directories"
grep -Fq "would remove" "$test_root/dry-output" ||
  fail "tidy dry-run did not report prune candidates"

run_tidy "$fixture" --apply > "$test_root/apply-output"
[[ -d "$fixture/standalone/releases/release-2" ]] ||
  fail "tidy apply removed the current release"
[[ -d "$fixture/standalone/releases/release-4" ]] ||
  fail "tidy apply removed the newest other release"
[[ ! -e "$fixture/standalone/releases/release-1" &&
   ! -e "$fixture/standalone/releases/release-3" ]] ||
  fail "tidy apply retained stale releases"
count=$(find "$fixture/standalone/releases" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
assert_eq 2 "$count" "tidy apply did not keep exactly two releases"
rm_calls=$(wc -l < "$test_root/rm.log" | tr -d ' ')
assert_eq 2 "$rm_calls" "tidy did not remove both stale releases through the guarded rm"

mounted_fixture="$test_root/mounted"
make_fixture "$mounted_fixture"
mounted_target="$mounted_fixture/standalone/releases/release-1/nested-mount"
if TEST_MOUNT_TARGET="$mounted_target" run_tidy "$mounted_fixture" --apply \
    > "$test_root/mounted-output" 2>&1; then
  fail "tidy accepted a mount beneath a prune target"
fi
count=$(find "$mounted_fixture/standalone/releases" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
assert_eq 4 "$count" "tidy changed releases with a nested mount reported"
grep -Fq "mount point exists at or beneath release" "$test_root/mounted-output" ||
  fail "tidy nested-mount refusal was not explicit"

findmnt_fixture="$test_root/findmnt-failure"
make_fixture "$findmnt_fixture"
if TEST_FINDMNT_STATUS=1 run_tidy "$findmnt_fixture" --apply \
    > "$test_root/findmnt-output" 2>&1; then
  fail "tidy ignored mount discovery failure"
fi
count=$(find "$findmnt_fixture/standalone/releases" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
assert_eq 4 "$count" "tidy changed releases after mount discovery failed"
grep -Fq "mount discovery failed before deleting" "$test_root/findmnt-output" ||
  fail "tidy mount-discovery refusal was not explicit"

empty_findmnt_fixture="$test_root/findmnt-empty"
make_fixture "$empty_findmnt_fixture"
if TEST_FINDMNT_EMPTY=1 run_tidy "$empty_findmnt_fixture" --apply \
    > "$test_root/findmnt-empty-output" 2>&1; then
  fail "tidy accepted an empty mount inventory"
fi
count=$(find "$empty_findmnt_fixture/standalone/releases" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
assert_eq 4 "$count" "tidy changed releases after empty mount discovery"
grep -Fq "mount discovery returned no parseable targets" "$test_root/findmnt-empty-output" ||
  fail "tidy empty-mount-inventory refusal was not explicit"

device_fixture="$test_root/device-mismatch"
make_fixture "$device_fixture"
mismatched_target=$(cd -P -- "$device_fixture/standalone/releases/release-1" && pwd)
if TEST_DEVICE_MISMATCH_PATH="$mismatched_target" run_tidy "$device_fixture" --apply \
    > "$test_root/device-output" 2>&1; then
  fail "tidy accepted a prune target on a different filesystem"
fi
count=$(find "$device_fixture/standalone/releases" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
assert_eq 4 "$count" "tidy changed releases after a device mismatch"
grep -Fq "release is on a different filesystem" "$test_root/device-output" ||
  fail "tidy device-mismatch refusal was not explicit"

locked_fixture="$test_root/locked"
make_fixture "$locked_fixture"
if TEST_LOCK_HELD=1 run_tidy "$locked_fixture" --apply \
    > "$test_root/locked-output" 2>&1; then
  fail "tidy accepted a held Codex install lock"
fi
count=$(find "$locked_fixture/standalone/releases" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
assert_eq 4 "$count" "tidy changed releases while the install lock was held"
grep -Fq "Codex install lock is held" "$test_root/locked-output" ||
  fail "tidy lock refusal was not explicit"
# findmnt -r reports "release old" as "release\x20old", which a literal prefix
# check cannot match, so such names must refuse before any deletion.
escaped_fixture="$test_root/escaped-name"
make_fixture "$escaped_fixture"
mkdir -p "$escaped_fixture/standalone/releases/release old"
touch -t 202512010101 "$escaped_fixture/standalone/releases/release old"
: > "$test_root/rm.log"
if TEST_MOUNT_TARGET="$escaped_fixture/standalone/releases/release\\x20old" \
    run_tidy "$escaped_fixture" --apply > "$test_root/escaped-output" 2>&1; then
  fail "tidy accepted a release name that mount discovery escapes"
fi
count=$(find "$escaped_fixture/standalone/releases" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
assert_eq 5 "$count" "tidy changed releases with an escaped-name release present"
rm_calls=$(wc -l < "$test_root/rm.log" | tr -d ' ')
assert_eq 0 "$rm_calls" "tidy invoked rm with an escaped-name release present"
grep -Fq "unsafe Codex release path" "$test_root/escaped-output" ||
  fail "tidy escaped-name refusal was not explicit"

outside_fixture="$test_root/outside"
make_fixture "$outside_fixture"
rm "$outside_fixture/standalone/current"
mkdir -p "$outside_fixture/not-a-release"
ln -s "$outside_fixture/not-a-release" "$outside_fixture/standalone/current"
if run_tidy "$outside_fixture" --apply > "$test_root/outside-output" 2>&1; then
  fail "tidy accepted a current symlink outside releases"
fi
count=$(find "$outside_fixture/standalone/releases" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
assert_eq 4 "$count" "tidy changed releases with an unsafe current symlink"
grep -Fq "current resolves outside releases" "$test_root/outside-output" ||
  fail "tidy outside-current refusal was not explicit"

echo "PASS: rootfs tidy dry-run, apply, lock, path, and mount safeguards"
