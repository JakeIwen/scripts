#!/bin/bash
# Level, forecast and path-history behavior of rootfs_budget.sh, driven through
# check_rootfs_budget with fixture df/du, history and a notify stub.

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

# shellcheck source=../lib.sh
source "$repo_root/pi/tests/lib.sh"
# shellcheck source=../../scripts/backup/rootfs_budget.sh
source "$repo_root/pi/scripts/backup/rootfs_budget.sh"

fake_bin="$test_root/bin"
mkdir -p "$fake_bin" "$test_root/stamps" "$test_root/paths/a"

# df reports used/avail in KiB from TEST_USED_PCT of a 100 GiB filesystem.
cat > "$fake_bin/df" <<'EOF2'
#!/bin/bash
total=$((100 * 1024 * 1024))
used=$((total * TEST_USED_PCT / 100))
printf 'Used Avail\n%s %s\n' "$used" "$((total - used))"
EOF2
cat > "$fake_bin/du" <<'EOF2'
#!/bin/bash
for path in "$@"; do :; done
printf '%s\t%s\n' "$TEST_DU_KIB" "$path"
EOF2
cat > "$fake_bin/timeout" <<'EOF2'
#!/bin/bash
[[ "$1" == -k ]] && shift 2
shift
exec "$@"
EOF2
cat > "$fake_bin/ionice" <<'EOF2'
#!/bin/bash
[[ "$1" == -c3 ]] && shift
exec "$@"
EOF2
cat > "$fake_bin/nice" <<'EOF2'
#!/bin/bash
[[ "$1" == -n ]] && shift 2
exec "$@"
EOF2
chmod +x "$fake_bin"/*

export TEST_DU_KIB=$((512 * 1024))
export TEST_PATH_A="$test_root/paths/a"
ROOTFS_DF_COMMAND="$fake_bin/df"
ROOTFS_DU_COMMAND="$fake_bin/du"
ROOTFS_TIMEOUT_COMMAND="$fake_bin/timeout"
ROOTFS_IONICE_COMMAND="$fake_bin/ionice"
ROOTFS_NICE_COMMAND="$fake_bin/nice"
ROOTFS_DU_TIMEOUT_SECONDS=45
ROOTFS_HISTORY_MAX_ENTRIES=120
ROOT_GROWTH_WARN_GIB_7D=4000
ROOT_USED_MAX_GB=1000
CLONE_CARD_NOMINAL_GB=64
STAMP_DIR="$test_root/stamps"
ROOTFS_HISTORY_FILE="$STAMP_DIR/history"
ROOTFS_ALERT_STATE_FILE="$STAMP_DIR/alert.state"
ROOTFS_PATH_HISTORY_FILE="$STAMP_DIR/paths.history"
ROOTFS_PATH_BUDGETS_MIB=("$TEST_PATH_A:1024")
ROOTFS_PATH_HISTORY_DAYS=400
sent_file="$test_root/sent"
: > "$sent_file"
notify_ok=0
notify() {
  printf '%s|%s|%s|%s|%s\n' "$1" "$2" "$3" "$4" "${NTFY_TOPIC_VAR:-}" >> "$sent_file"
  return $notify_ok
}

now=$((300 * 86400 + 3600))
run_check() {
  problems=()
  status=""
  check_rootfs_budget "$now"
}
sent_count() { grep -c '^' "$sent_file"; }
state() { grep -h "^$1=" "$ROOTFS_ALERT_STATE_FILE" 2>/dev/null; }

# No history: only level alerts apply. 69% is quiet.
TEST_USED_PCT=69
export TEST_USED_PCT
run_check
assert_eq 0 "$(sent_count)" "69% alerted"

# Crossing 70 alerts once, on the warnings topic.
TEST_USED_PCT=70
run_check
assert_eq 1 "$(sent_count)" "70% did not alert once"
grep -Fq 'crossed 70%' "$sent_file" || fail "message does not name the level"
grep -Fq '|NTFY_WARNING_URL' "$sent_file" || fail "alert not sent on the warnings topic"
assert_eq "level70=1" "$(state level70)" "level70 not recorded"

# A second run at the same level is silent, as is a rise inside the level.
run_check
TEST_USED_PCT=75
run_check
assert_eq 1 "$(sent_count)" "70% alerted again"

# Falling to 66 (inside the 5-point band) keeps it armed-off; 64 re-arms.
TEST_USED_PCT=66
run_check
assert_eq "level70=1" "$(state level70)" "level70 re-armed inside the hysteresis band"
TEST_USED_PCT=64
run_check
assert_eq "level70=0" "$(state level70)" "level70 did not re-arm at 64%"
TEST_USED_PCT=70
run_check
assert_eq 2 "$(sent_count)" "re-armed level did not alert again"

# Jumping past two levels sends one message for the highest and marks both.
: > "$ROOTFS_ALERT_STATE_FILE"
: > "$sent_file"
TEST_USED_PCT=91
run_check
assert_eq 1 "$(sent_count)" "multi-level crossing sent more than one message"
grep -Fq 'crossed 90%' "$sent_file" || fail "multi-level message is not for 90%"
assert_eq "level70=1" "$(state level70)" "level70 not marked on multi-level jump"
assert_eq "level80=1" "$(state level80)" "level80 not marked on multi-level jump"
run_check
assert_eq 1 "$(sent_count)" "multi-level jump repeated"

# A failed send leaves the level armed so the next run retries.
rm -f "$ROOTFS_ALERT_STATE_FILE"
: > "$sent_file"
notify_ok=1
TEST_USED_PCT=71
run_check 2>/dev/null
assert_eq 1 "$(sent_count)" "failed send was not attempted"
[[ -z "$(state level70)" ]] || fail "failed send marked the level alerted"
notify_ok=0
run_check
assert_eq 2 "$(sent_count)" "failed send was not retried"
assert_eq "level70=1" "$(state level70)" "retry did not record the level"

# The test override forces the reading and does not repeat.
rm -f "$ROOTFS_ALERT_STATE_FILE"
: > "$sent_file"
TEST_USED_PCT=10
ROOTFS_TEST_PERCENT=91
run_check
run_check
assert_eq 1 "$(sent_count)" "forced reading repeated"
grep -Fq 'forced by ROOTFS_TEST_PERCENT' "$sent_file" || fail "forced reading not labelled"
unset ROOTFS_TEST_PERCENT

# Forecast: 10 daily points rising 5 GiB/day to 50 GiB; the 90 GiB line is
# about 8 days out.
rm -f "$ROOTFS_ALERT_STATE_FILE" "$ROOTFS_HISTORY_FILE"
: > "$sent_file"
TEST_USED_PCT=50
day=$((now / 86400 - 10))
while (( day < now / 86400 )); do
  used_mib=$(( 50 * 1024 - ((now / 86400) - day) * 5 * 1024 ))
  printf '%s %s %s\n' "$day" "$((day * 86400 + 3600))" "$used_mib" >> "$ROOTFS_HISTORY_FILE"
  day=$((day + 1))
done
run_check
assert_eq 1 "$(sent_count)" "forecast did not fire once"
grep -Fq 'reaches 90% on' "$sent_file" || fail "forecast message lacks the projected date"
assert_eq "forecast=1" "$(state forecast)" "forecast state not recorded"
run_check
assert_eq 1 "$(sent_count)" "forecast repeated"

# Flat history clears it (state re-arms silently).
: > "$ROOTFS_HISTORY_FILE"
day=$((now / 86400 - 10))
while (( day < now / 86400 )); do
  printf '%s %s %s\n' "$day" "$((day * 86400 + 3600))" $((50 * 1024)) >> "$ROOTFS_HISTORY_FILE"
  day=$((day + 1))
done
run_check
assert_eq "forecast=0" "$(state forecast)" "forecast did not clear"
assert_eq 1 "$(sent_count)" "clearing the forecast sent a message"

# Fewer than 7 points never forecasts.
rm -f "$ROOTFS_ALERT_STATE_FILE"
: > "$sent_file"
: > "$ROOTFS_HISTORY_FILE"
for ago in 3 2 1; do
  printf '%s %s %s\n' "$((now / 86400 - ago))" "$(( (now / 86400 - ago) * 86400 + 3600))" $((50 * 1024 - ago * 5 * 1024)) >> "$ROOTFS_HISTORY_FILE"
done
run_check
assert_eq 0 "$(sent_count)" "forecast fired on fewer than 7 points"

# Per-path budget: over budget is a problem; sizes are recorded daily, with
# the same-day line replaced and lines older than the retention dropped.
TEST_DU_KIB=$((2 * 1024 * 1024))
export TEST_DU_KIB
ROOTFS_PATH_HISTORY_DAYS=100
printf '%s %s %s\n' 1 86400 "$TEST_PATH_A:1" > "$ROOTFS_PATH_HISTORY_FILE"
printf '%s %s %s\n' $((now / 86400 - 10)) 0 "$TEST_PATH_A:7" >> "$ROOTFS_PATH_HISTORY_FILE"
run_check
run_check
all_problems=$(printf '%s\n' "${problems[@]}")
[[ "$all_problems" == *"$TEST_PATH_A uses 2.0GiB (budget 1.0GiB)"* ]] ||
  fail "over-budget path not reported"
assert_eq 2 "$(grep -c '^' "$ROOTFS_PATH_HISTORY_FILE")" "path history not pruned or same-day line duplicated"
grep -Fq "$((now / 86400)) $now $TEST_PATH_A:2048" "$ROOTFS_PATH_HISTORY_FILE" ||
  fail "today's path size was not recorded"
grep -q '^1 ' "$ROOTFS_PATH_HISTORY_FILE" && fail "stale path history line kept"

echo "PASS: rootfs level, forecast, and path-history alerts"
