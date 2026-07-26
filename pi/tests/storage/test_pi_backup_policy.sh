#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
backup_script="$repo_root/pi/scripts/backup/pi_backup.sh"
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

fake_conf="$test_root/backup_conf.sh"
fake_policyctl="$test_root/policyctl"
policy_calls="$test_root/policy-calls"
stamp_dir="$test_root/stamps"
ignition_flag="$test_root/ignition_is_on"
mkdir -p "$stamp_dir"

cat > "$fake_conf" <<EOF
STAMP_DIR='$stamp_dir'
IGNITION_FLAG='$ignition_flag'
acquire_job_lock() { return 1; }
EOF
cat > "$fake_policyctl" <<'HELPER'
#!/bin/bash
printf '%s\n' "$*" >> "$TEST_POLICY_CALLS"
printf '%s\n' "${TEST_POLICY_OUTPUT:-0 1 0}"
exit "${TEST_POLICY_STATUS:-0}"
HELPER
chmod +x "$fake_policyctl"

run_backup() {
  TEST_POLICY_CALLS="$policy_calls" \
    PI_BACKUP_CONF="$fake_conf" \
    PI_BACKUP_POLICYCTL="$fake_policyctl" \
    bash "$backup_script" "$@"
}

: > "$policy_calls"
output=$(TEST_POLICY_OUTPUT="0 1 0" run_backup 2>&1) ||
  fail "disabled requested policy was treated as a backup failure"
[[ "$output" == *"requested policy disables HDDs"* ]] ||
  fail "disabled-policy deferral was not clearly logged"
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
[[ "$output" == *"requested policy disables HDDs"* ]] ||
  fail "manual --force mounted despite disabled requested policy"

run_backup --unknown >/dev/null 2>&1
unknown_status=$?
if (( unknown_status == 0 )); then
  fail "unknown backup option was accepted"
fi
[[ $unknown_status == 2 ]] ||
  fail "unknown backup option did not return usage status 2"

grep -Fq '"$mount_disks" "$label" || return 1' "$backup_script" ||
  fail "backup does not delegate mounting to the shared exact-label helper"
if grep -Eq 'umount[[:space:]]+-l' "$backup_script" ||
   grep -Fq 'mount "$dev" "$mnt"' "$backup_script"; then
  fail "backup retained its old lazy-unmount or direct-mount implementation"
fi

echo "PASS: unattended backups obey requested HDD policy"
