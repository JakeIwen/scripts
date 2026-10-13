#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

# shellcheck source=../lib.sh
source "$repo_root/pi/tests/lib.sh"
# shellcheck source=../../scripts/backup/rootfs_budget.sh
source "$repo_root/pi/scripts/backup/rootfs_budget.sh"

fake_bin="$test_root/bin"
mkdir -p "$fake_bin" "$test_root/stamps" "$test_root/paths/a" "$test_root/paths/b"

cat > "$fake_bin/df" <<'EOF'
#!/bin/bash
if (( ${TEST_DF_STATUS:-0} != 0 )); then
  exit "$TEST_DF_STATUS"
fi
printf 'Used\n%s\n' "${TEST_ROOT_USED_KIB:?}"
EOF
cat > "$fake_bin/du" <<'EOF'
#!/bin/bash
for path in "$@"; do :; done
if [[ "$path" == "${TEST_DU_FAIL_PATH:-}" ]]; then
  exit 1
elif [[ "$path" == "$TEST_PATH_A" ]]; then
  kib=$TEST_DU_A_KIB
elif [[ "$path" == "$TEST_PATH_B" ]]; then
  kib=$TEST_DU_B_KIB
else
  exit 2
fi
printf '%s\t%s\n' "$kib" "$path"
EOF
cat > "$fake_bin/timeout" <<'EOF'
#!/bin/bash
[[ "$1" == -k ]] && shift 2
shift
exec "$@"
EOF
cat > "$fake_bin/ionice" <<'EOF'
#!/bin/bash
[[ "$1" == -c3 ]] && shift
exec "$@"
EOF
cat > "$fake_bin/nice" <<'EOF'
#!/bin/bash
[[ "$1" == -n ]] && shift 2
exec "$@"
EOF
chmod +x "$fake_bin"/*

export TEST_PATH_A="$test_root/paths/a"
export TEST_PATH_B="$test_root/paths/b"
export TEST_DU_A_KIB=$((1536 * 1024))
export TEST_DU_B_KIB=$((1024 * 1024))
export TEST_ROOT_USED_KIB=$((10 * 1024 * 1024))
ROOTFS_DF_COMMAND="$fake_bin/df"
ROOTFS_DU_COMMAND="$fake_bin/du"
ROOTFS_TIMEOUT_COMMAND="$fake_bin/timeout"
ROOTFS_IONICE_COMMAND="$fake_bin/ionice"
ROOTFS_NICE_COMMAND="$fake_bin/nice"
ROOTFS_DU_TIMEOUT_SECONDS=45
ROOTFS_HISTORY_MAX_ENTRIES=120
ROOT_GROWTH_WARN_GIB_7D=4
ROOT_USED_MAX_GB=52
CLONE_CARD_NOMINAL_GB=64
STAMP_DIR="$test_root/stamps"
ROOTFS_HISTORY_FILE="$STAMP_DIR/history"
ROOTFS_PATH_BUDGETS_MIB=("$TEST_PATH_A:1024" "$TEST_PATH_B:2048")
now=$((200 * 86400 + 3600))

run_check() {
  problems=()
  status=""
  check_rootfs_budget "$now"
}

problem_text() {
  local index=0
  while (( index < ${#problems[@]} )); do
    printf '%s\n' "${problems[index]}"
    index=$((index + 1))
  done
}

run_check
all_problems=$(problem_text)
[[ "$all_problems" == *"$TEST_PATH_A uses 1.5GiB (budget 1.0GiB)"* ]] ||
  fail "over-budget path was not reported"
[[ "$all_problems" != *"$TEST_PATH_B uses"* ]] ||
  fail "under-budget path was reported"
expected_context="rootfs paths, largest first: $TEST_PATH_A=1.5GiB; $TEST_PATH_B=1.0GiB"
[[ "$all_problems" == *"$expected_context"* ]] ||
  fail "rootfs context was not sorted largest first"

export TEST_DU_FAIL_PATH=$TEST_PATH_A
run_check
all_problems=$(problem_text)
[[ "$all_problems" == *"could not measure rootfs path $TEST_PATH_A"* ]] ||
  fail "du failure was not counted as a problem"
[[ "$all_problems" == *"$TEST_PATH_A=measurement failed"* ]] ||
  fail "du failure was absent from rootfs context"
unset TEST_DU_FAIL_PATH

ROOTFS_PATH_BUDGETS_MIB=("$TEST_PATH_A:4096" "$TEST_PATH_B:4096")
baseline_epoch=$((now - 7 * 86400))
baseline_day=$((baseline_epoch / 86400))
printf '%s %s %s\n' "$baseline_day" "$baseline_epoch" $((5 * 1024)) > "$ROOTFS_HISTORY_FILE"
run_check
all_problems=$(problem_text)
[[ "$all_problems" == *"rootfs grew 5.0GiB in the last 7d"* ]] ||
  fail "growth above threshold was not reported"
[[ "$all_problems" == *"$expected_context"* ]] ||
  fail "growth alert omitted measured rootfs path context"

printf '%s %s %s\n' "$baseline_day" "$baseline_epoch" $((7 * 1024)) > "$ROOTFS_HISTORY_FILE"
run_check
all_problems=$(problem_text)
[[ "$all_problems" != *"rootfs grew"* ]] ||
  fail "growth below threshold was reported"

: > "$ROOTFS_HISTORY_FILE"
day=$((now / 86400 - 130))
while (( day < now / 86400 )); do
  printf '%s %s %s\n' "$day" "$((day * 86400 + 3600))" $((6 * 1024)) \
    >> "$ROOTFS_HISTORY_FILE"
  day=$((day + 1))
done
run_check
line_count=$(grep -c '^' "$ROOTFS_HISTORY_FILE")
assert_eq 120 "$line_count" "rootfs history was not bounded"
run_check
line_count=$(grep -c '^' "$ROOTFS_HISTORY_FILE")
assert_eq 120 "$line_count" "same-day rootfs reading was appended twice"

# Exercise the watchdog notification boundary with all Linux-specific commands
# stubbed; unrelated missing-stamp findings are expected in this isolated fixture.
watchdog_conf="$test_root/backup_conf.sh"
disk_policy="$test_root/disk_policy.sh"
notify="$test_root/notify.sh"
findmnt="$fake_bin/findmnt"
blkid="$fake_bin/blkid"
message_file="$test_root/notification"
cat > "$watchdog_conf" <<EOF
STAMP_DIR=$test_root/watchdog-stamps
BORG_STALE_HOURS=48
EXFAT_SNAPSHOT_STAMP=$test_root/no-exfat-stamp
EXFAT_SNAPSHOT_STALE_HOURS=48
EXFAT_SNAPSHOT_DISK_LABEL=no-device
OPENWRT_SNAPSHOT_FILE=$test_root/no-openwrt
OPENWRT_BACKUP_STAMP=$test_root/no-openwrt-stamp
OPENWRT_BACKUP_STALE_HOURS=72
UBNT_SNAPSHOT_FILE=$test_root/no-ubnt
UBNT_BACKUP_STAMP=$test_root/no-ubnt-stamp
UBNT_BACKUP_STALE_HOURS=72
CLONE_TARGETS=(fixture-clone:7)
CLONE_STALE_FACTOR=2
ROOT_USED_MAX_GB=52
CLONE_CARD_NOMINAL_GB=64
ROOT_GROWTH_WARN_GIB_7D=4
ROOTFS_HISTORY_FILE=$test_root/watchdog-stamps/history
ROOTFS_HISTORY_MAX_ENTRIES=120
ROOTFS_DU_TIMEOUT_SECONDS=45
ROOTFS_PATH_BUDGETS_MIB=("$TEST_PATH_A:1024" "$TEST_PATH_B:2048")
BACKUP_MNT=$test_root/not-mounted
BACKUP_DISK_LABEL=bigboi
MIN_FREE_GB=100
UNMOUNT_AFTER=1
IGNITION_FLAG=$test_root/no-ignition
EOF
cat > "$disk_policy" <<'EOF'
disk_policy_resolve_exact_label() {
  DISK_POLICY_RESOLVE_ERROR="fixture label is absent"
  return 1
}
EOF
cat > "$notify" <<EOF
#!/bin/bash
printf '%s\n---\n%s\n' "\$1" "\$2" > "$message_file"
EOF
cat > "$findmnt" <<'EOF'
#!/bin/bash
[[ "${*: -1}" == / ]] && printf '%s\n' /dev/null
EOF
cat > "$blkid" <<'EOF'
#!/bin/bash
exit 1
EOF
chmod +x "$notify" "$findmnt" "$blkid"
mkdir -p "$test_root/watchdog-stamps"
PATH="$fake_bin:$PATH" \
BACKUP_WATCHDOG_CONF="$watchdog_conf" \
BACKUP_WATCHDOG_DISK_POLICY="$disk_policy" \
BACKUP_WATCHDOG_NOTIFY="$notify" \
ROOTFS_DF_COMMAND="$fake_bin/df" \
ROOTFS_DU_COMMAND="$fake_bin/du" \
ROOTFS_TIMEOUT_COMMAND="$fake_bin/timeout" \
ROOTFS_IONICE_COMMAND="$fake_bin/ionice" \
ROOTFS_NICE_COMMAND="$fake_bin/nice" \
bash "$repo_root/pi/scripts/backup/backup_watchdog.sh" >/dev/null
[[ -s "$message_file" ]] || fail "watchdog did not call the ntfy stub"
grep -Fq "$expected_context" "$message_file" ||
  fail "watchdog alert omitted measured rootfs path context"

echo "PASS: rootfs growth, budgets, history, and alert context"
