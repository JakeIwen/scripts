#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
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

rm "$test_root/ignition"
: > "$calls"
repair_root="$test_root/repair"
health_dir="$repair_root/health"
mount_root="$repair_root/mnt"
fake_device="$repair_root/exfat-device"
fake_parent="$repair_root/exfat-parent"
mkdir -p "$mount_root/EXFAT512"
touch "$fake_device" "$fake_parent"

fake_sudo="$repair_root/sudo"
fake_blkid="$repair_root/blkid"
fake_readlink="$repair_root/readlink"
fake_lsblk="$repair_root/lsblk"
fake_findmnt="$repair_root/findmnt"
fake_fsck="$repair_root/fsck.exfat"
fake_install="$repair_root/install"
fake_timeout="$repair_root/timeout"
fake_by_label="$repair_root/by-label"
fake_sys_block="$repair_root/sys-block"
fake_udevadm="$repair_root/udevadm"
mkdir -p "$fake_by_label" "$fake_sys_block/exfat-device"
ln -s "$fake_device" "$fake_by_label/EXFAT512"
ln -s "$fake_device" "$fake_by_label/movingparts"
ln -s "$fake_device" "$fake_by_label/bigboi"

cat > "$fake_sudo" <<'EOF'
#!/bin/bash
"$@"
EOF
cat > "$fake_blkid" <<EOF
#!/bin/bash
case "\$*" in
  *"-s LABEL -o value"*) printf '%s\\n' "\${TEST_REPAIR_LABEL:-EXFAT512}" ;;
  *"-s TYPE -o value"*) printf '%s\\n' "\${TEST_REPAIR_FSTYPE:-exfat}" ;;
  *) exit 2 ;;
esac
EOF
cat > "$fake_readlink" <<EOF
#!/bin/bash
printf '%s\\n' "$fake_device"
EOF
cat > "$fake_lsblk" <<EOF
#!/bin/bash
if [[ "\$*" == *"-s -nrpo NAME,TYPE"* ]]; then
  printf '%s part\\n%s disk\\n' "$fake_device" "$fake_parent"
elif [[ "\$*" == *"-dnro TRAN"* ]]; then
  printf '%s\\n' usb
else
  exit 2
fi
EOF
cat > "$fake_findmnt" <<EOF
#!/bin/bash
if [[ "\$*" == *"-S $fake_device"* ]]; then
  exit 1
elif [[ "\$*" == *"-M $mount_root/"* ]]; then
  if [[ "\${TEST_WAS_MOUNTED:-1}" == 1 ]]; then
    printf '%s\\n' "$fake_device"
  else
    exit 1
  fi
else
  exit 2
fi
EOF
cat > "$fake_fsck" <<EOF
#!/bin/bash
printf 'fsck %s\\n' "\$*" >> "$calls"
printf '%s\\n' 'exFAT filesystem is clean'
EOF
cat > "$fake_install" <<'EOF'
#!/bin/bash
if [[ " $* " == *" -d "* ]]; then
  mkdir -p "${!#}"
else
  target=${!#}
  source=${@: -2:1}
  mkdir -p "$(dirname "$target")"
  cp "$source" "$target"
fi
EOF
cat > "$fake_timeout" <<'EOF'
#!/bin/bash
shift
"$@"
EOF
cat > "$fake_udevadm" <<EOF
#!/bin/bash
printf '%s\n' \
  "DEVNAME=$fake_device" \
  "ID_FS_LABEL=\${TEST_REPAIR_LABEL:-EXFAT512}"
EOF
chmod +x "$fake_sudo" "$fake_blkid" "$fake_readlink" "$fake_lsblk" \
  "$fake_findmnt" "$fake_fsck" "$fake_install" "$fake_timeout" "$fake_udevadm"

DISK_EJECT_HOLD_DIR="$hold_dir" DISK_EJECT_NOW=1000000 \
DISK_HEALTH_STATE_DIR="$health_dir" \
DISKCTL_UNMOUNT_DISKS="$fake_unmount" \
DISKCTL_MOUNT_DISKS="$fake_mount" \
DISKCTL_IGNITION_FLAG="$test_root/ignition" \
DISKCTL_LIFECYCLE_LOCK="$test_root/lifecycle.lock" \
DISKCTL_FLOCK="$fake_flock" \
DISKCTL_SUDO="$fake_sudo" \
DISKCTL_BLKID="$fake_blkid" \
DISKCTL_BY_LABEL_DIR="$fake_by_label" \
DISKCTL_SYS_BLOCK_DIR="$fake_sys_block" \
DISKCTL_UDEVADM="$fake_udevadm" \
DISKCTL_FINDMNT="$fake_findmnt" \
DISKCTL_READLINK="$fake_readlink" \
DISKCTL_LSBLK="$fake_lsblk" \
DISKCTL_FSCK_EXFAT="$fake_fsck" \
DISKCTL_FSCK_EXT4="$fake_fsck" \
DISKCTL_PYTHON="$(command -v python3)" \
DISKCTL_INSTALL="$fake_install" \
DISKCTL_RM=/bin/rm \
DISKCTL_TOUCH=/usr/bin/touch \
DISKCTL_DATE=/bin/date \
DISKCTL_MKTEMP=/usr/bin/mktemp \
DISKCTL_MOUNT_ROOT="$mount_root" \
DISKCTL_REQUIRE_BLOCK_DEVICE=0 \
DISKCTL_TIMEOUT="$fake_timeout" \
bash "$repo_root/pi/scripts/diskctl" repair EXFAT512 >/dev/null ||
  fail "verified exFAT repair workflow failed"

expected_repair_calls=$'unmount EXFAT512\nfsck -p -- '"$fake_device"$'\nfsck -n -- '"$fake_device"$'\nmount EXFAT512'
[[ $(cat "$calls") == "$expected_repair_calls" ]] ||
  fail "repair did not unmount, repair, verify, and remount in order"
[[ ! -e "$health_dir/quarantine/EXFAT512" ]] ||
  fail "successful repair left the filesystem quarantined"
python3 - "$health_dir/EXFAT512.json" <<'PY' ||
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["label"] == "EXFAT512"
assert payload["state"] == "healthy"
PY
  fail "successful repair did not record healthy state"

: > "$calls"
mkdir -p "$mount_root/movingparts"
TEST_REPAIR_LABEL=movingparts TEST_REPAIR_FSTYPE=ext4 \
DISK_EJECT_HOLD_DIR="$hold_dir" DISK_EJECT_NOW=1000000 \
DISK_HEALTH_STATE_DIR="$health_dir" \
DISKCTL_UNMOUNT_DISKS="$fake_unmount" \
DISKCTL_MOUNT_DISKS="$fake_mount" \
DISKCTL_INTERNET_SWITCHES="$fake_reconcile" \
DISKCTL_POLICYCTL="$fake_policy" \
DISKCTL_IGNITION_FLAG="$test_root/ignition" \
DISKCTL_LIFECYCLE_LOCK="$test_root/lifecycle.lock" \
DISKCTL_FLOCK="$fake_flock" \
DISKCTL_SUDO="$fake_sudo" \
DISKCTL_BLKID="$fake_blkid" \
DISKCTL_BY_LABEL_DIR="$fake_by_label" \
DISKCTL_SYS_BLOCK_DIR="$fake_sys_block" \
DISKCTL_UDEVADM="$fake_udevadm" \
DISKCTL_FINDMNT="$fake_findmnt" \
DISKCTL_READLINK="$fake_readlink" \
DISKCTL_LSBLK="$fake_lsblk" \
DISKCTL_FSCK_EXFAT="$fake_fsck" \
DISKCTL_FSCK_EXT4="$fake_fsck" \
DISKCTL_PYTHON="$(command -v python3)" \
DISKCTL_INSTALL="$fake_install" \
DISKCTL_RM=/bin/rm \
DISKCTL_TOUCH=/usr/bin/touch \
DISKCTL_DATE=/bin/date \
DISKCTL_MKTEMP=/usr/bin/mktemp \
DISKCTL_MOUNT_ROOT="$mount_root" \
DISKCTL_REQUIRE_BLOCK_DEVICE=0 \
DISKCTL_TIMEOUT="$fake_timeout" \
bash "$repo_root/pi/scripts/diskctl" repair movingparts >/dev/null ||
  fail "verified ext4 repair workflow failed"

expected_ext4_calls=$'unmount movingparts\nfsck -pf -- '"$fake_device"$'\nfsck -fn -- '"$fake_device"$'\nmount movingparts\nreconcile'
[[ $(cat "$calls") == "$expected_ext4_calls" ]] ||
  fail "ext4 repair did not preserve mount state and restore policy in order"
[[ ! -e "$health_dir/quarantine/movingparts" ]] ||
  fail "successful ext4 repair left the filesystem quarantined"
python3 - "$health_dir/movingparts.json" <<'PY' ||
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["label"] == "movingparts"
assert payload["state"] == "healthy"
assert payload["message"] == "ext4 repaired, verified clean, and remounted"
PY
  fail "successful ext4 repair did not record healthy state"

: > "$calls"
mkdir -p "$mount_root/bigboi"
TEST_REPAIR_LABEL=bigboi TEST_REPAIR_FSTYPE=ext4 TEST_WAS_MOUNTED=0 \
DISK_EJECT_HOLD_DIR="$hold_dir" DISK_EJECT_NOW=1000000 \
DISK_HEALTH_STATE_DIR="$health_dir" \
DISKCTL_UNMOUNT_DISKS="$fake_unmount" \
DISKCTL_MOUNT_DISKS="$fake_mount" \
DISKCTL_INTERNET_SWITCHES="$fake_reconcile" \
DISKCTL_POLICYCTL="$fake_policy" \
DISKCTL_IGNITION_FLAG="$test_root/ignition" \
DISKCTL_LIFECYCLE_LOCK="$test_root/lifecycle.lock" \
DISKCTL_FLOCK="$fake_flock" \
DISKCTL_SUDO="$fake_sudo" \
DISKCTL_BLKID="$fake_blkid" \
DISKCTL_BY_LABEL_DIR="$fake_by_label" \
DISKCTL_SYS_BLOCK_DIR="$fake_sys_block" \
DISKCTL_UDEVADM="$fake_udevadm" \
DISKCTL_FINDMNT="$fake_findmnt" \
DISKCTL_READLINK="$fake_readlink" \
DISKCTL_LSBLK="$fake_lsblk" \
DISKCTL_FSCK_EXFAT="$fake_fsck" \
DISKCTL_FSCK_EXT4="$fake_fsck" \
DISKCTL_PYTHON="$(command -v python3)" \
DISKCTL_INSTALL="$fake_install" \
DISKCTL_RM=/bin/rm \
DISKCTL_TOUCH=/usr/bin/touch \
DISKCTL_DATE=/bin/date \
DISKCTL_MKTEMP=/usr/bin/mktemp \
DISKCTL_MOUNT_ROOT="$mount_root" \
DISKCTL_REQUIRE_BLOCK_DEVICE=0 \
DISKCTL_TIMEOUT="$fake_timeout" \
bash "$repo_root/pi/scripts/diskctl" repair bigboi >/dev/null ||
  fail "unmounted ext4 repair workflow failed"

expected_unmounted_calls=$'unmount bigboi\nfsck -pf -- '"$fake_device"$'\nfsck -fn -- '"$fake_device"
[[ $(cat "$calls") == "$expected_unmounted_calls" ]] ||
  fail "repair did not preserve an ext4 disk's unmounted state"
python3 - "$health_dir/bigboi.json" <<'PY' ||
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["state"] == "healthy"
assert payload["message"].endswith("preserved unmounted state")
PY
  fail "unmounted ext4 repair did not record preserved state"

echo "PASS: diskctl label, hold, policy, and ignition safeguards"
