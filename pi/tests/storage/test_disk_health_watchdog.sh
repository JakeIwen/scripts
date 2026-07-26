#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

state_dir="$test_root/state"
health_dir="$test_root/health"
mount_root="$test_root/mnt"
calls="$test_root/calls"
fake_device="$test_root/exfat-device"
mkdir -p "$mount_root/EXFAT512" "$health_dir/quarantine"
touch "$fake_device" "$calls"

fake_findmnt="$test_root/findmnt"
fake_blkid="$test_root/blkid"
fake_readlink="$test_root/readlink"
fake_sudo="$test_root/sudo"
fake_timeout="$test_root/timeout"
fake_find="$test_root/find"
fake_touch="$test_root/touch"
fake_diskctl="$test_root/diskctl"

cat > "$fake_findmnt" <<EOF
#!/bin/bash
printf '%s\\n' "$fake_device"
EOF
cat > "$fake_blkid" <<EOF
#!/bin/bash
case "\$*" in
  *"-t LABEL=EXFAT512 -o device"*) printf '%s\\n' "$fake_device" ;;
  *"-s LABEL -o value"*) printf '%s\\n' EXFAT512 ;;
  *"-s TYPE -o value"*) printf '%s\\n' exfat ;;
  *) exit 2 ;;
esac
EOF
cat > "$fake_readlink" <<EOF
#!/bin/bash
printf '%s\\n' "$fake_device"
EOF
cat > "$fake_sudo" <<'EOF'
#!/bin/bash
[[ "$1" == -u && "$2" == pi ]] && shift 2
"$@"
EOF
cat > "$fake_timeout" <<'EOF'
#!/bin/bash
shift
"$@"
EOF
cat > "$fake_find" <<'EOF'
#!/bin/bash
exit "${TEST_READ_STATUS:-1}"
EOF
cat > "$fake_touch" <<'EOF'
#!/bin/bash
if (( ${TEST_WRITE_STATUS:-1} == 0 )); then
  /usr/bin/touch "$1"
  exit 0
fi
exit "${TEST_WRITE_STATUS:-1}"
EOF
cat > "$fake_diskctl" <<EOF
#!/bin/bash
printf 'diskctl %s\\n' "\$*" >> "$calls"
exit "\${TEST_REPAIR_STATUS:-0}"
EOF
chmod +x "$fake_findmnt" "$fake_blkid" "$fake_readlink" "$fake_sudo" \
  "$fake_timeout" "$fake_find" "$fake_touch" "$fake_diskctl"

run_watchdog() {
  DISK_HEALTH_STATE_DIR="$health_dir" \
  DISK_HEALTH_WATCH_STATE_DIR="$state_dir" \
  DISK_HEALTH_WATCH_MOUNT_ROOT="$mount_root" \
  DISK_HEALTH_WATCH_IGNITION_FLAG="$test_root/ignition" \
  DISK_HEALTH_WATCH_DISKCTL="$fake_diskctl" \
  DISK_HEALTH_WATCH_FINDMNT="$fake_findmnt" \
  DISK_HEALTH_WATCH_BLKID="$fake_blkid" \
  DISK_HEALTH_WATCH_READLINK="$fake_readlink" \
  DISK_HEALTH_WATCH_SUDO="$fake_sudo" \
  DISK_HEALTH_WATCH_TIMEOUT="$fake_timeout" \
  DISK_HEALTH_WATCH_FIND="$fake_find" \
  DISK_HEALTH_WATCH_TOUCH="$fake_touch" \
  DISK_HEALTH_WATCH_DATE=/bin/date \
  DISK_HEALTH_WATCH_RM=/bin/rm \
  DISK_HEALTH_WATCH_MV=/bin/mv \
  TEST_READ_STATUS="${TEST_READ_STATUS:-1}" \
  TEST_WRITE_STATUS="${TEST_WRITE_STATUS:-1}" \
  TEST_REPAIR_STATUS="${TEST_REPAIR_STATUS:-0}" \
  bash "$repo_root/pi/scripts/disk_health_watchdog.sh"
}

run_watchdog >/dev/null || fail "first unusable observation failed"
[[ -f "$state_dir/EXFAT512.unusable" ]] ||
  fail "first unusable observation was not recorded"
[[ ! -s "$calls" ]] || fail "watchdog repaired without confirmation"

run_watchdog >/dev/null || fail "confirmed unusable mount was not repaired"
[[ $(cat "$calls") == "diskctl repair EXFAT512" ]] ||
  fail "watchdog did not invoke exact-label repair"
[[ ! -e "$state_dir/EXFAT512.unusable" ]] ||
  fail "successful repair retained unusable state"

: > "$calls"
TEST_READ_STATUS=0 run_watchdog >/dev/null ||
  fail "readable read-only mount was treated as a failure"
[[ ! -s "$calls" ]] ||
  fail "watchdog automatically repaired a readable read-only mount"

TEST_READ_STATUS=1 TEST_WRITE_STATUS=0 run_watchdog >/dev/null ||
  fail "writable mount was treated as a failure"
[[ ! -s "$calls" ]] || fail "watchdog automatically repaired a writable mount"

TEST_READ_STATUS=1 TEST_WRITE_STATUS=1 run_watchdog >/dev/null ||
  fail "could not re-arm unusable observation"
TEST_REPAIR_STATUS=1 run_watchdog >/dev/null 2>&1 &&
  fail "repair failure was reported as success"
[[ -f "$state_dir/EXFAT512.cooldown" ]] ||
  fail "repair failure did not create a cooldown"
call_count=$(wc -l < "$calls")
TEST_REPAIR_STATUS=0 run_watchdog >/dev/null ||
  fail "cooldown check failed"
[[ $(wc -l < "$calls") == "$call_count" ]] ||
  fail "watchdog retried during repair cooldown"

rm -f "$state_dir/EXFAT512.cooldown"
touch "$health_dir/quarantine/EXFAT512"
run_watchdog >/dev/null ||
  fail "quarantined filesystem caused watchdog failure"
[[ $(wc -l < "$calls") == "$call_count" ]] ||
  fail "watchdog retried a quarantined filesystem"

echo "PASS: disk-health watchdog repairs only confirmed wholly unusable exFAT mounts"
