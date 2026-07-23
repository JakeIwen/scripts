#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

mount_root="$test_root/mnt"
label_root="$test_root/by-label"
source_root="$test_root/dev"
fake_findmnt="$test_root/findmnt"
mkdir -p "$mount_root/mbp1tbkup" "$mount_root/mbp2tbkup" \
  "$label_root" "$source_root"
touch "$source_root/mbp1-device" "$source_root/mbp2-device" "$source_root/wrong-device"
ln -s "$source_root/mbp1-device" "$label_root/mbp1tbkup"
ln -s "$source_root/mbp2-device" "$label_root/mbp2tbkup"

cat > "$fake_findmnt" <<'HELPER'
#!/bin/bash
[[ $1 == -rn && $2 == -M && $4 == -o && $5 == SOURCE ]] || exit 99
case "$2:$3" in
  -M:*/mbp1tbkup) printf '%s\n' "$TEST_MBP1_SOURCE" ;;
  -M:*/mbp2tbkup) printf '%s\n' "$TEST_MBP2_SOURCE" ;;
  *) exit 1 ;;
esac
HELPER
chmod +x "$fake_findmnt"

run_gate() {
  SAMBA_MOUNT_GATE_FINDMNT="$fake_findmnt" \
    SAMBA_MOUNT_GATE_MOUNT_ROOT="$mount_root" \
    SAMBA_MOUNT_GATE_LABEL_ROOT="$label_root" \
    SAMBA_MOUNT_GATE_REQUIRE_BLOCK_DEVICE=0 \
    TEST_MBP1_SOURCE="${TEST_MBP1_SOURCE:-$source_root/mbp1-device}" \
    TEST_MBP2_SOURCE="${TEST_MBP2_SOURCE:-$source_root/mbp2-device}" \
    bash "$repo_root/pi/scripts/samba_require_mount.sh" "$@"
}

run_gate mbp1tbkup >/dev/null || fail "exact mbp1tbkup mount was rejected"
run_gate mbp2tbkup >/dev/null || fail "exact mbp2tbkup mount was rejected"

TEST_MBP2_SOURCE="$source_root/wrong-device"
export TEST_MBP2_SOURCE
run_gate mbp2tbkup >/dev/null 2>&1 &&
  fail "wrong filesystem mounted at mbp2tbkup was accepted"
unset TEST_MBP2_SOURCE

mv "$mount_root/mbp2tbkup" "$mount_root/mbp2tbkup-real"
ln -s "$mount_root/mbp2tbkup-real" "$mount_root/mbp2tbkup"
run_gate mbp2tbkup >/dev/null 2>&1 &&
  fail "symlink Time Machine target was accepted"
rm "$mount_root/mbp2tbkup"
mv "$mount_root/mbp2tbkup-real" "$mount_root/mbp2tbkup"

rm "$label_root/mbp2tbkup"
run_gate mbp2tbkup >/dev/null 2>&1 &&
  fail "missing filesystem label was accepted"

run_gate movingparts >/dev/null 2>&1 &&
  fail "non-Time-Machine label was accepted"

for share in mbp1tbkup mbp2tbkup; do
  grep -Fq "preexec = /home/pi/scripts/samba_require_mount.sh $share" \
    "$repo_root/pi/configs/smb.conf" ||
    fail "$share does not use the exact mount gate"
done
[[ $(grep -Fc "preexec close = yes" "$repo_root/pi/configs/smb.conf") == 2 ]] ||
  fail "Time Machine shares must close when their mount gate fails"

echo "PASS: Time Machine Samba shares require their exact mounted labels"
