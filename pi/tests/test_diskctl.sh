#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

hold_dir="$test_root/holds"
calls="$test_root/calls"
fake_unmount="$test_root/umount_disks.sh"
fake_mount="$test_root/mount_disks.sh"
fake_policy="$test_root/policyctl"
fake_reconcile="$test_root/internet_switches.sh"
fake_flock="$test_root/flock"
: > "$calls"

cat > "$fake_unmount" <<EOF
#!/bin/bash
printf 'unmount %s\n' "\$*" >> "$calls"
EOF
cat > "$fake_mount" <<EOF
#!/bin/bash
printf 'mount %s\n' "\$*" >> "$calls"
EOF
cat > "$fake_policy" <<'EOF'
#!/bin/bash
printf '%s\n' "${TEST_DISK_POLICY:-1 1 0}"
EOF
cat > "$fake_reconcile" <<EOF
#!/bin/bash
printf 'reconcile\n' >> "$calls"
EOF
cat > "$fake_flock" <<'EOF'
#!/bin/bash
[[ $1 == -w && $2 == 55 && $3 == 9 ]]
EOF
chmod +x "$fake_unmount" "$fake_mount" "$fake_policy" "$fake_reconcile" "$fake_flock"

run_diskctl() {
  DISK_EJECT_HOLD_DIR="$hold_dir" DISK_EJECT_NOW=1000000 \
    DISKCTL_UNMOUNT_DISKS="$fake_unmount" \
    DISKCTL_MOUNT_DISKS="$fake_mount" \
    DISKCTL_POLICYCTL="$fake_policy" \
    DISKCTL_INTERNET_SWITCHES="$fake_reconcile" \
    DISKCTL_IGNITION_FLAG="$test_root/ignition" \
    DISKCTL_LIFECYCLE_LOCK="$test_root/lifecycle.lock" \
    DISKCTL_FLOCK="$fake_flock" \
    TEST_DISK_POLICY="${TEST_DISK_POLICY:-1 1 0}" \
    bash "$repo_root/pi/scripts/diskctl" "$@"
}

run_diskctl eject movingparts >/dev/null || fail "managed disk eject failed"
[[ $(cat "$hold_dir/movingparts") == 1000060 ]] || fail "eject hold deadline was incorrect"
[[ $(cat "$calls") == "unmount movingparts" ]] || fail "eject did not use the exact label"

DISK_EJECT_HOLD_DIR="$hold_dir"
DISK_EJECT_NOW=1000030
export DISK_EJECT_HOLD_DIR DISK_EJECT_NOW
source "$repo_root/pi/scripts/disk_policy.sh"
remaining=$(disk_eject_hold_remaining movingparts) || fail "live hold was not detected"
[[ $remaining == 30 ]] || fail "hold remaining time was incorrect"

: > "$calls"
run_diskctl mount movingparts >/dev/null || fail "managed disk mount failed"
[[ ! -e "$hold_dir/movingparts" ]] || fail "manual mount did not clear eject hold"
[[ $(cat "$calls") == "reconcile" ]] || fail "mount did not use policy reconciliation"

: > "$calls"
run_diskctl eject bigboi >/dev/null || fail "manual disk eject failed"
[[ ! -e "$hold_dir/bigboi" ]] || fail "manual disk eject created an automatic-mount hold"
[[ $(cat "$calls") == "unmount bigboi" ]] || fail "manual eject did not use the exact label"

: > "$calls"
run_diskctl mount bigboi >/dev/null || fail "manual disk mount failed"
[[ $(cat "$calls") == "mount bigboi" ]] || fail "manual mount did not use the exact label"

: > "$calls"
TEST_DISK_POLICY="0 1 0"
export TEST_DISK_POLICY
printf '1000060\n' > "$hold_dir/EXFAT512"
run_diskctl mount EXFAT512 >/dev/null || fail "always-mounted flash was blocked by disabled HDD policy"
[[ ! -e "$hold_dir/EXFAT512" ]] || fail "flash remount did not clear eject hold"
[[ $(cat "$calls") == "mount EXFAT512" ]] || fail "flash remount did not use the exact label"

for label in /dev/sda unknown; do
  run_diskctl eject "$label" >/dev/null 2>&1 && fail "unsafe label '$label' was accepted"
done

mkdir -p "$hold_dir"
printf '1000060\n' > "$hold_dir/movingparts"
run_diskctl mount movingparts >/dev/null 2>&1 && fail "mount bypassed disabled disk policy"
[[ $(cat "$hold_dir/movingparts") == 1000060 ]] || fail "policy refusal cleared eject hold"

touch "$test_root/ignition"
TEST_DISK_POLICY="1 1 0"
run_diskctl mount movingparts >/dev/null 2>&1 && fail "mount bypassed ignition state"
[[ $(cat "$hold_dir/movingparts") == 1000060 ]] || fail "ignition refusal cleared eject hold"

: > "$calls"
printf '1000060\n' > "$hold_dir/EXFAT512"
run_diskctl mount EXFAT512 >/dev/null || fail "ignition blocked always-mounted flash"
[[ ! -e "$hold_dir/EXFAT512" ]] || fail "ignition-time flash remount did not clear hold"
[[ $(cat "$calls") == "mount EXFAT512" ]] || fail "ignition-time flash mount was not exact"

echo "PASS: diskctl label, hold, policy, and ignition safeguards"
