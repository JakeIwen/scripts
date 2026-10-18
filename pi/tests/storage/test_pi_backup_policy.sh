#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
backup_script="$repo_root/pi/scripts/backup/pi_backup.sh"
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

# shellcheck source=../lib.sh
source "$repo_root/pi/tests/lib.sh"

fake_conf="$test_root/backup_conf.sh"
fake_policyctl="$test_root/policyctl"
policy_calls="$test_root/policy-calls"
stamp_dir="$test_root/stamps"
ignition_flag="$test_root/ignition_is_on"
mkdir -p "$stamp_dir"

cat > "$fake_conf" <<EOF
STAMP_DIR='$stamp_dir'
IGNITION_FLAG='$ignition_flag'
acquire_backup_job() { return 1; }
EOF
cat > "$fake_policyctl" <<'HELPER'
#!/bin/bash
printf '%s\n' "$*" >> "$TEST_POLICY_CALLS"
printf '%s\n' "${TEST_POLICY_OUTPUT:-0 1 0}"
exit "${TEST_POLICY_STATUS:-0}"
HELPER
chmod +x "$fake_policyctl"

# Records every mount request and refuses it, so a backup that reaches the
# mount step is visible without touching a real disk.
fake_mount_disks="$test_root/mount_disks"
mount_calls="$test_root/mount-calls"
cat > "$fake_mount_disks" <<'HELPER'
#!/bin/bash
printf '%s\n' "$*" >> "$TEST_MOUNT_CALLS"
exit 1
HELPER
chmod +x "$fake_mount_disks"

run_backup() {
  TEST_POLICY_CALLS="$policy_calls" \
    TEST_MOUNT_CALLS="$mount_calls" \
    PI_BACKUP_CONF="${TEST_BACKUP_CONF:-$fake_conf}" \
    PI_BACKUP_POLICYCTL="$fake_policyctl" \
    PI_BACKUP_MOUNT_DISKS="$fake_mount_disks" \
    PI_BACKUP_PROGRESS_HELPER="$repo_root/pi/scripts/backup/progress.sh" \
    bash "$backup_script" "$@"
}

: > "$policy_calls"
output=$(TEST_POLICY_OUTPUT="0 1 0" run_backup 2>&1) ||
  fail "disabled requested policy was treated as a backup failure"
[[ $(cat "$policy_calls") == read ]] ||
  fail "backup did not read requested policy through policyctl"

: > "$policy_calls"
TEST_POLICY_OUTPUT="1 1 0" run_backup >/dev/null 2>&1 ||
  fail "enabled policy did not reach the normal already-running exit"
[[ $(cat "$policy_calls") == read ]] ||
  fail "enabled backup did not verify requested policy"

: > "$policy_calls"
if TEST_POLICY_OUTPUT="garbage" run_backup >/dev/null 2>&1; then
  fail "malformed requested policy was accepted"
fi

: > "$policy_calls"
if TEST_POLICY_OUTPUT=$'1 1 0\nunexpected' run_backup >/dev/null 2>&1; then
  fail "multi-line requested policy output was accepted"
fi

: > "$policy_calls"
if TEST_POLICY_STATUS=1 run_backup >/dev/null 2>&1; then
  fail "unreadable requested policy was accepted"
fi

: > "$policy_calls"
touch "$ignition_flag"
TEST_POLICY_OUTPUT="1 1 0" run_backup --force >/dev/null 2>&1 ||
  fail "explicit manual override did not retain the ignition deferral"
[[ $(cat "$policy_calls") == read ]] ||
  fail "manual --force bypassed requested HDD policy"
rm "$ignition_flag"

: > "$policy_calls"
output=$(TEST_POLICY_OUTPUT="0 1 0" run_backup --force 2>&1) ||
  fail "disabled policy during --force was treated as a backup failure"
[[ ! -s "$mount_calls" ]] ||
  fail "manual --force mounted despite disabled requested policy"

# The spindown fence stops only jobs that already hold their disk locks, so a
# flag raised between the pre-lock check and the locks must still defer the
# job before it mounts anything.
late_ignition_conf="$test_root/backup_conf_late_ignition.sh"
cat > "$late_ignition_conf" <<EOF
STAMP_DIR='$stamp_dir'
SNAP_DIR='$test_root/snapshots'
IGNITION_FLAG='$ignition_flag'
BACKUP_DISK_LABEL=bigboi
BACKUP_MNT='$test_root/mnt/bigboi'
MEDIA_SRC='$test_root/mnt/movingparts'
acquire_backup_job() { touch '$ignition_flag'; return 0; }
EOF
: > "$policy_calls"
: > "$mount_calls"
output=$(TEST_POLICY_OUTPUT="1 1 0" TEST_BACKUP_CONF="$late_ignition_conf" run_backup 2>&1)
late_status=$?
rm -f "$ignition_flag"
[[ $late_status == 0 ]] ||
  fail "ignition raised while locking was not deferred cleanly (status $late_status): $output"
[[ ! -s "$mount_calls" ]] ||
  fail "backup asked to mount a disk after the van started: $(cat "$mount_calls")"
[[ ! -e "$stamp_dir/progress/borg.state" ]] ||
  fail "post-lock ignition deferral started progress telemetry"

run_backup --unknown >/dev/null 2>&1
unknown_status=$?
if (( unknown_status == 0 )); then
  fail "unknown backup option was accepted"
fi
[[ $unknown_status == 2 ]] ||
  fail "unknown backup option did not return usage status 2"

echo "PASS: unattended backups obey requested HDD policy"
