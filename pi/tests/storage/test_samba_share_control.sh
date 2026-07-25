#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

calls="$test_root/calls"
fake_systemctl="$test_root/systemctl"
fake_smbcontrol="$test_root/smbcontrol"
fake_sudo="$test_root/sudo"
: > "$calls"

cat > "$fake_systemctl" <<'HELPER'
#!/bin/bash
[[ "$*" == "show --property=ActiveState --value smbd.service" ]] || exit 99
printf '%s\n' "${TEST_SMBD_STATE:-active}"
exit "${TEST_SYSTEMCTL_STATUS:-0}"
HELPER
cat > "$fake_smbcontrol" <<'HELPER'
#!/bin/bash
printf 'smbcontrol:%s\n' "$*" >> "$TEST_SAMBA_CALLS"
exit "${TEST_SMBCONTROL_STATUS:-0}"
HELPER
cat > "$fake_sudo" <<'HELPER'
#!/bin/bash
"$@"
HELPER
chmod +x "$fake_systemctl" "$fake_smbcontrol" "$fake_sudo"

run_control() {
  TEST_SAMBA_CALLS="$calls" \
    SAMBA_SHARE_CONTROL_SYSTEMCTL="$fake_systemctl" \
    SAMBA_SHARE_CONTROL_SMBCONTROL="$fake_smbcontrol" \
    SAMBA_SHARE_CONTROL_SUDO="$fake_sudo" \
    TEST_SMBD_STATE="${TEST_SMBD_STATE:-active}" \
    TEST_SYSTEMCTL_STATUS="${TEST_SYSTEMCTL_STATUS:-0}" \
    TEST_SMBCONTROL_STATUS="${TEST_SMBCONTROL_STATUS:-0}" \
    bash "$repo_root/pi/scripts/samba_share_control.sh" "$@"
}

run_control close movingparts bigboi mbp2tbkup EXFAT512 >/dev/null ||
  fail "active Samba shares did not close"
expected=$'smbcontrol:smbd close-share MovingParts\nsmbcontrol:smbd close-share BigBoi\nsmbcontrol:smbd close-share mbp2tbkup\nsmbcontrol:smbd close-share EXFAT512'
[[ $(cat "$calls") == "$expected" ]] ||
  fail "share control did not target exact configured share names"

: > "$calls"
run_control close hfs2tb usbext >/dev/null ||
  fail "labels without Samba shares were treated as failures"
[[ ! -s "$calls" ]] || fail "unshared labels invoked smbcontrol"

: > "$calls"
TEST_SMBD_STATE=inactive run_control close bigboi >/dev/null ||
  fail "inactive smbd was treated as a close failure"
[[ ! -s "$calls" ]] || fail "inactive smbd invoked smbcontrol"

TEST_SMBD_STATE=activating run_control close bigboi >/dev/null 2>&1 &&
  fail "transitional smbd state was accepted"
TEST_SMBCONTROL_STATUS=1 run_control close bigboi >/dev/null 2>&1 &&
  fail "smbcontrol failure was ignored"
run_control close unknown >/dev/null 2>&1 &&
  fail "unmanaged label was accepted"

grep -Fq '"$samba_share_control" close "${mounted_labels[@]}"' \
  "$repo_root/pi/scripts/umount_disks.sh" ||
  fail "disk unmount does not close only the selected Samba shares"
if grep -Fq '/usr/sbin/service smbd stop' "$repo_root/pi/scripts/umount_disks.sh"; then
  fail "disk unmount still stops global Samba"
fi
if grep -Eq 'pgrep[[:space:]]+-fi|pk[[:space:]]+qbit' "$repo_root/pi/scripts/remount.sh"; then
  fail "manual remount still uses a fuzzy qBittorrent process killer"
fi

echo "PASS: Samba share control is label-scoped and fail-closed"
