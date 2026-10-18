#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

calls="$test_root/calls"
fake_unmount="$test_root/umount_disks.sh"
fake_sync="$test_root/sync"
fake_sudo="$test_root/sudo"
fake_systemctl="$test_root/systemctl"
fake_flock="$test_root/flock"
: > "$calls"

cat > "$fake_unmount" <<'HELPER'
#!/bin/bash
printf 'unmount:%s\n' "$*" >> "$TEST_SYSTEM_POWER_CALLS"
exit "${TEST_UNMOUNT_STATUS:-0}"
HELPER
cat > "$fake_sync" <<'HELPER'
#!/bin/bash
printf 'sync\n' >> "$TEST_SYSTEM_POWER_CALLS"
exit "${TEST_SYNC_STATUS:-0}"
HELPER
cat > "$fake_sudo" <<'HELPER'
#!/bin/bash
"$@"
HELPER
cat > "$fake_systemctl" <<'HELPER'
#!/bin/bash
printf 'systemctl:%s\n' "$*" >> "$TEST_SYSTEM_POWER_CALLS"
exit "${TEST_SYSTEMCTL_STATUS:-0}"
HELPER
cat > "$fake_flock" <<'HELPER'
#!/bin/bash
[[ "$1" == -w && "$2" -gt 0 && "$3" -ge 3 ]] || exit 99
exit "${TEST_FLOCK_STATUS:-0}"
HELPER
chmod +x "$fake_unmount" "$fake_sync" "$fake_sudo" "$fake_systemctl" "$fake_flock"

run_power_script() {
  TEST_SYSTEM_POWER_CALLS="$calls" \
    SAFE_SYSTEM_POWER_UNMOUNT_DISKS="$fake_unmount" \
    SAFE_SYSTEM_POWER_SYNC="$fake_sync" \
    SAFE_SYSTEM_POWER_SUDO="$fake_sudo" \
    SAFE_SYSTEM_POWER_SYSTEMCTL="$fake_systemctl" \
    SAFE_SYSTEM_POWER_FLOCK="$fake_flock" \
    SAFE_SYSTEM_POWER_LIFECYCLE_LOCK="$test_root/lifecycle.lock" \
    TEST_UNMOUNT_STATUS="${TEST_UNMOUNT_STATUS:-0}" \
    TEST_SYNC_STATUS="${TEST_SYNC_STATUS:-0}" \
    TEST_SYSTEMCTL_STATUS="${TEST_SYSTEMCTL_STATUS:-0}" \
    TEST_FLOCK_STATUS="${TEST_FLOCK_STATUS:-0}" \
    "$1"
}

run_power_script "$repo_root/pi/scripts/safe_reboot.sh" >/dev/null ||
  fail "safe reboot failed"
expected=$'unmount:--all\nsync\nsystemctl:reboot'
[[ $(cat "$calls") == "$expected" ]] ||
  fail "safe reboot did not unmount, sync, then reboot"

: > "$calls"
run_power_script "$repo_root/pi/scripts/safe_power_down.sh" >/dev/null ||
  fail "safe power down failed"
expected=$'unmount:--all\nsync\nsystemctl:poweroff'
[[ $(cat "$calls") == "$expected" ]] ||
  fail "safe power down did not unmount, sync, then power off"

: > "$calls"
TEST_UNMOUNT_STATUS=1 run_power_script \
  "$repo_root/pi/scripts/safe_reboot.sh" >/dev/null 2>&1 &&
  fail "reboot continued after an unmount failure"
[[ $(cat "$calls") == "unmount:--all" ]] ||
  fail "unmount failure did not stop before sync and reboot"

: > "$calls"
TEST_SYNC_STATUS=1 run_power_script \
  "$repo_root/pi/scripts/safe_power_down.sh" >/dev/null 2>&1 &&
  fail "power down continued after a sync failure"
expected=$'unmount:--all\nsync'
[[ $(cat "$calls") == "$expected" ]] ||
  fail "sync failure did not stop before poweroff"

: > "$calls"
TEST_FLOCK_STATUS=1 run_power_script \
  "$repo_root/pi/scripts/safe_reboot.sh" >/dev/null 2>&1 &&
  fail "reboot continued without the lifecycle lock"
[[ ! -s "$calls" ]] || fail "lock failure changed disk or power state"

"$repo_root/pi/scripts/system_power.sh" halt >/dev/null 2>&1 &&
  fail "unsupported system power action was accepted"

echo "PASS: reboot and power-down scripts are serialized and fail closed"
