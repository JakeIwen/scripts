#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
script="$repo_root/pi/scripts/backup/new_hotspare.sh"
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

fake_conf="$test_root/backup_conf.sh"
fake_policy="$test_root/disk_policy.sh"
fake_lsblk="$test_root/lsblk"
fake_findmnt="$test_root/findmnt"
fake_readlink="$test_root/readlink"
fake_udevadm="$test_root/udevadm"
fake_timeout="$test_root/timeout"
fake_clone="$test_root/clone_to_sd.sh"
clone_calls="$test_root/clone-calls"

cat > "$fake_conf" <<'EOF'
CLONE_TARGETS=(hotspare-a:7 hotspare-b:14)
CLONE_MAX_DISK_GB=500
CLONE_USB_READER_IDS=(1234:5678 abcd:ef01)
CLONE_SPARE_SIZE_TOLERANCE_GB=4
acquire_job_lock() { return "${TEST_LOCK_STATUS:-0}"; }
EOF
cat > "$fake_policy" <<'EOF'
DISK_POLICY_RESOLVED_DEVICE=
DISK_POLICY_RESOLVE_ERROR=
disk_policy_resolve_exact_label() {
  DISK_POLICY_RESOLVED_DEVICE=
  DISK_POLICY_RESOLVE_ERROR=
  case "${TEST_LABEL_STATE:-a-present}:$1" in
    a-present:hotspare-a) DISK_POLICY_RESOLVED_DEVICE=/dev/sda2; return 0 ;;
    a-present:hotspare-b) return 1 ;;
    b-present:hotspare-a) return 1 ;;
    b-present:hotspare-b) DISK_POLICY_RESOLVED_DEVICE=/dev/sda2; return 0 ;;
    both-present:hotspare-a) DISK_POLICY_RESOLVED_DEVICE=/dev/sda2; return 0 ;;
    both-present:hotspare-b) DISK_POLICY_RESOLVED_DEVICE=/dev/sdb2; return 0 ;;
    both-missing:*) return 1 ;;
    unsafe:hotspare-a) DISK_POLICY_RESOLVE_ERROR='duplicate label'; return 2 ;;
    unsafe:hotspare-b) return 1 ;;
    *) return 1 ;;
  esac
}
EOF
cat > "$fake_lsblk" <<'EOF'
#!/bin/bash
args=" $* "
target=${!#}
case "$args" in
  *" -s -nrpo NAME,TYPE "*)
    case "$target" in
      /dev/mmcblk0p2) printf '/dev/mmcblk0p2 part\n/dev/mmcblk0 disk\n' ;;
      /dev/sda2) printf '/dev/sda2 part\n/dev/sda disk\n' ;;
      /dev/sdb2) printf '/dev/sdb2 part\n/dev/sdb disk\n' ;;
      *) exit 1 ;;
    esac
    ;;
  *" -dnrpo NAME,TYPE "*)
    printf '/dev/mmcblk0 disk\n/dev/sda disk\n/dev/sdb disk\n'
    [[ "${TEST_EXTRA_CANDIDATE:-0}" == 1 ]] && printf '/dev/sdc disk\n'
    exit 0
    ;;
  *" -dnro TRAN "*)
    case "$target" in
      /dev/sd*) printf 'usb\n' ;;
      *) printf 'mmc\n' ;;
    esac
    ;;
  *" -dnro RM "*)
    case "$target" in
      /dev/sd*) printf '1\n' ;;
      *) printf '0\n' ;;
    esac
    ;;
  *" -bdnro SIZE "*)
    case "$target" in
      /dev/sda) printf '%s\n' "${TEST_ATTACHED_SIZE:-34359738368}" ;;
      *) printf '%s\n' "${TEST_CARD_SIZE:-34359738368}" ;;
    esac
    ;;
  *" -nrpo NAME,MOUNTPOINTS "*)
    printf '%s\n' "$target" "${target}1"
    if [[ "${TEST_CANDIDATE_MOUNTED:-0}" == 1 && "$target" == /dev/sdb ]]; then
      printf '%s /mnt/wrong-card\n' "${target}2"
    else
      printf '%s\n' "${target}2"
    fi
    ;;
  *" -o NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINTS "*)
    printf 'NAME SIZE TYPE FSTYPE LABEL MOUNTPOINTS\n%s 32G disk\n' "$target"
    ;;
  *)
    echo "unexpected fake lsblk call: $*" >&2
    exit 1
    ;;
esac
EOF
cat > "$fake_findmnt" <<'EOF'
#!/bin/bash
[[ "$*" == '-nro SOURCE /' ]] || exit 1
printf '/dev/mmcblk0p2\n'
EOF
cat > "$fake_readlink" <<'EOF'
#!/bin/bash
printf '%s\n' "${!#}"
EOF
cat > "$fake_udevadm" <<'EOF'
#!/bin/bash
target=${!#}
case "$target" in
  --name=/dev/sda)
    printf 'ID_BUS=usb\nID_VENDOR_ID=1234\nID_MODEL_ID=5678\n'
    ;;
  --name=/dev/sdb)
    if [[ "${TEST_READER_APPROVED:-1}" == 1 ]]; then
      printf 'ID_BUS=usb\nID_VENDOR_ID=abcd\nID_MODEL_ID=ef01\n'
    else
      printf 'ID_BUS=usb\nID_VENDOR_ID=9999\nID_MODEL_ID=0001\n'
    fi
    ;;
  --name=/dev/sdc)
    if [[ "${TEST_THIRD_READER_MATCHES:-1}" == 1 ]]; then
      printf 'ID_BUS=usb\nID_VENDOR_ID=abcd\nID_MODEL_ID=ef01\n'
    else
      printf 'ID_BUS=usb\nID_VENDOR_ID=9999\nID_MODEL_ID=0001\n'
    fi
    ;;
  *) exit 1 ;;
esac
EOF
cat > "$fake_timeout" <<'EOF'
#!/bin/bash
shift 3
exec "$@"
EOF
cat > "$fake_clone" <<EOF
#!/bin/bash
printf '%s\n' "\$*" >> "$clone_calls"
exit "\${TEST_CLONE_STATUS:-0}"
EOF
chmod +x "$fake_lsblk" "$fake_findmnt" "$fake_readlink" \
  "$fake_udevadm" "$fake_timeout" "$fake_clone"

run_script() {
  NEW_HOTSPARE_CONF="$fake_conf" \
    NEW_HOTSPARE_DISK_POLICY="$fake_policy" \
    NEW_HOTSPARE_CLONE_TOOL="$fake_clone" \
    NEW_HOTSPARE_LSBLK="$fake_lsblk" \
    NEW_HOTSPARE_FINDMNT="$fake_findmnt" \
    NEW_HOTSPARE_READLINK="$fake_readlink" \
    NEW_HOTSPARE_UDEVADM="$fake_udevadm" \
    NEW_HOTSPARE_TIMEOUT="$fake_timeout" \
    NEW_HOTSPARE_REQUIRE_ROOT=0 \
    NEW_HOTSPARE_REQUIRE_BLOCK_DEVICE=0 \
    bash "$script" "$@"
}

: > "$clone_calls"
output=$(run_script --yes 2>&1) || fail "one changed card was not initialized: $output"
[[ "$output" == *"Detected missing hot-spare generation: hotspare-b"* ]] ||
  fail "missing hotspare-b was not detected"
[[ "$output" == *"Approved replacement USB reader: abcd:ef01"* ]] ||
  fail "a different approved reader model was not accepted"
[[ $(cat "$clone_calls") == "--init hotspare-b sdb" ]] ||
  fail "initializer did not receive the detected label and whole disk"

: > "$clone_calls"
output=$(TEST_LABEL_STATE=b-present run_script --dry-run 2>&1) ||
  fail "dry-run could not detect missing hotspare-a: $output"
[[ "$output" == *"Detected missing hot-spare generation: hotspare-a"* ]] ||
  fail "missing hotspare-a was not detected"
[[ ! -s "$clone_calls" ]] || fail "dry-run invoked the clone initializer"

: > "$clone_calls"
if TEST_LABEL_STATE=both-missing run_script --yes >/dev/null 2>&1; then
  fail "two missing hot spares were treated as an identifiable replacement"
fi
[[ ! -s "$clone_calls" ]] || fail "ambiguous labels invoked the clone initializer"

: > "$clone_calls"
if TEST_EXTRA_CANDIDATE=1 run_script --yes >/dev/null 2>&1; then
  fail "two matching replacement cards were accepted"
fi
[[ ! -s "$clone_calls" ]] || fail "ambiguous candidates invoked the clone initializer"

: > "$clone_calls"
if TEST_CANDIDATE_MOUNTED=1 run_script --yes >/dev/null 2>&1; then
  fail "a mounted replacement card was accepted"
fi
[[ ! -s "$clone_calls" ]] || fail "mounted media invoked the clone initializer"

: > "$clone_calls"
if TEST_READER_APPROVED=0 run_script --yes >/dev/null 2>&1; then
  fail "an unapproved replacement reader was accepted"
fi
[[ ! -s "$clone_calls" ]] || fail "unapproved reader invoked the initializer"

: > "$clone_calls"
if TEST_CARD_SIZE=68719476736 run_script --yes >/dev/null 2>&1; then
  fail "a replacement outside the spare size tolerance was accepted"
fi
[[ ! -s "$clone_calls" ]] || fail "wrong-sized media invoked the initializer"

: > "$clone_calls"
if TEST_LABEL_STATE=unsafe run_script --yes >/dev/null 2>&1; then
  fail "an unsafe label mapping was accepted"
fi
[[ ! -s "$clone_calls" ]] || fail "unsafe label discovery invoked the initializer"

: > "$clone_calls"
if TEST_LOCK_STATUS=1 run_script --yes >/dev/null 2>&1; then
  fail "a busy backup lock was ignored"
fi
[[ ! -s "$clone_calls" ]] || fail "busy backup lock invoked the initializer"

: > "$clone_calls"
if TEST_CLONE_STATUS=9 run_script --yes >/dev/null 2>&1; then
  fail "clone failure was reported as success"
fi
[[ $(cat "$clone_calls") == "--init hotspare-b sdb" ]] ||
  fail "clone failure test did not reach the initializer"

echo "PASS: new hot-spare replacement detection fails closed"
