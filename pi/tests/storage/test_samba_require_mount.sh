#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

mount_root="$test_root/mnt"
label_root="$test_root/by-label"
source_root="$test_root/dev"
drain_root="$test_root/drain"
fake_findmnt="$test_root/findmnt"
gated_labels=(mbp2tbkup movingparts bigboi EXFAT512)
mkdir -p "$label_root" "$source_root" "$drain_root"
for label in "${gated_labels[@]}"; do
  mkdir -p "$mount_root/$label"
  touch "$source_root/$label-device"
  ln -s "$source_root/$label-device" "$label_root/$label"
done
touch "$source_root/wrong-device"

cat > "$fake_findmnt" <<'HELPER'
#!/bin/bash
[[ $1 == -rn && $2 == -M && $4 == -o && $5 == SOURCE ]] || exit 99
label=${3##*/}
if [[ "$label" == mbp2tbkup && -n ${TEST_MBP2_SOURCE:-} ]]; then
  printf '%s\n' "$TEST_MBP2_SOURCE"
else
  printf '%s/%s-device\n' "$TEST_SOURCE_ROOT" "$label"
fi
HELPER
chmod +x "$fake_findmnt"

run_gate() {
  SAMBA_MOUNT_GATE_FINDMNT="$fake_findmnt" \
    SAMBA_MOUNT_GATE_MOUNT_ROOT="$mount_root" \
    SAMBA_MOUNT_GATE_LABEL_ROOT="$label_root" \
    SAMBA_MOUNT_GATE_REQUIRE_BLOCK_DEVICE=0 \
    SAMBA_SHARE_DRAIN_DIR="$drain_root" \
    TEST_SOURCE_ROOT="$source_root" \
    bash "$repo_root/pi/scripts/samba_require_mount.sh" "$@"
}

for label in "${gated_labels[@]}"; do
  run_gate "$label" >/dev/null || fail "exact $label mount was rejected"
done

touch "$drain_root/movingparts"
run_gate movingparts >/dev/null 2>&1 &&
  fail "draining Samba share accepted a reconnect"
rm "$drain_root/movingparts"
run_gate movingparts >/dev/null ||
  fail "cleared Samba drain continued blocking the exact share"

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

run_gate unshared-test >/dev/null 2>&1 &&
  fail "non-Samba label was accepted"

for label in "${gated_labels[@]}"; do
  grep -Fq "preexec = /home/pi/scripts/samba_require_mount.sh $label" \
    "$repo_root/pi/configs/smb.conf" ||
    fail "$label does not use the exact mount gate"
done
[[ $(grep -Fc "preexec close = yes" "$repo_root/pi/configs/smb.conf") == 4 ]] ||
  fail "all disk-backed Samba shares must close when their mount gate fails"

echo "PASS: disk-backed Samba shares require their exact mounted labels"
