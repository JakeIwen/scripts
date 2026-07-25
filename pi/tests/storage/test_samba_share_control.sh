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
fake_pgrep="$test_root/pgrep"
fake_sleep="$test_root/sleep"
drain_dir="$test_root/drain"
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
case "$1" in
  /usr/bin/install)
    shift
    if [[ "$1" == -d ]]; then
      target=${!#}
      mkdir -p "$target"
    else
      target=${!#}
      : > "$target"
    fi
    ;;
  /usr/bin/mv|/usr/bin/rm)
    command=${1##*/}
    shift
    "$command" "$@"
    ;;
  *) "$@" ;;
esac
HELPER
cat > "$fake_pgrep" <<'HELPER'
#!/bin/bash
[[ "$*" == "-x smbd" ]] || exit 99
exit "${TEST_SMBD_PGREP_STATUS:-1}"
HELPER
cat > "$fake_sleep" <<'HELPER'
#!/bin/bash
[[ "$*" == 1 ]] || exit 99
HELPER
chmod +x "$fake_systemctl" "$fake_smbcontrol" "$fake_sudo" \
  "$fake_pgrep" "$fake_sleep"

run_control() {
  TEST_SAMBA_CALLS="$calls" \
    SAMBA_SHARE_CONTROL_SYSTEMCTL="$fake_systemctl" \
    SAMBA_SHARE_CONTROL_SMBCONTROL="$fake_smbcontrol" \
    SAMBA_SHARE_CONTROL_SUDO="$fake_sudo" \
    SAMBA_SHARE_CONTROL_PGREP="$fake_pgrep" \
    SAMBA_SHARE_CONTROL_SLEEP="$fake_sleep" \
    SAMBA_SHARE_DRAIN_DIR="$drain_dir" \
    SAMBA_SHARE_STATE_WAIT_SECONDS="${SAMBA_SHARE_STATE_WAIT_SECONDS:-0}" \
    TEST_SMBD_STATE="${TEST_SMBD_STATE:-active}" \
    TEST_SMBD_PGREP_STATUS="${TEST_SMBD_PGREP_STATUS:-1}" \
    TEST_SYSTEMCTL_STATUS="${TEST_SYSTEMCTL_STATUS:-0}" \
    TEST_SMBCONTROL_STATUS="${TEST_SMBCONTROL_STATUS:-0}" \
    bash "$repo_root/pi/scripts/samba_share_control.sh" "$@"
}

run_control close movingparts bigboi mbp2tbkup EXFAT512 >/dev/null ||
  fail "active Samba shares did not close"
expected=$'smbcontrol:-t 3 smbd close-share MovingParts\nsmbcontrol:-t 3 smbd close-share BigBoi\nsmbcontrol:-t 3 smbd close-share mbp2tbkup\nsmbcontrol:-t 3 smbd close-share EXFAT512'
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

TEST_SMBD_STATE=failed TEST_SMBD_PGREP_STATUS=1 \
  run_control close movingparts bigboi >/dev/null ||
  fail "failed smbd with no processes was not treated as already closed"

state_error=$(
  TEST_SMBD_STATE=failed TEST_SMBD_PGREP_STATUS=0 \
    run_control close movingparts bigboi 2>&1
) && fail "failed smbd with live processes was accepted"
grep -Fq "Samba shares MovingParts, BigBoi" <<< "$state_error" ||
  fail "failed-state diagnostic omitted the affected Samba share names"

state_error=$(TEST_SMBD_STATE=activating run_control close movingparts bigboi 2>&1) &&
  fail "transitional smbd state was accepted"
grep -Fq "cannot close Samba shares MovingParts, BigBoi" <<< "$state_error" ||
  fail "transitional-state diagnostic omitted the affected Samba share names"
TEST_SMBCONTROL_STATUS=1 run_control close bigboi >/dev/null 2>&1 &&
  fail "smbcontrol failure was ignored"
run_control close unknown >/dev/null 2>&1 &&
  fail "unmanaged label was accepted"

run_control drain movingparts bigboi >/dev/null ||
  fail "could not drain exact Samba shares"
[[ -f "$drain_dir/movingparts" && -f "$drain_dir/bigboi" ]] ||
  fail "drain markers were not created by filesystem label"
run_control clear bigboi >/dev/null || fail "could not clear exact Samba drain"
[[ -f "$drain_dir/movingparts" && ! -e "$drain_dir/bigboi" ]] ||
  fail "clearing one drain modified the wrong Samba share"

grep -Fq '"$samba_share_control" close "${mounted_labels[@]}"' \
  "$repo_root/pi/scripts/umount_disks.sh" ||
  fail "disk unmount does not close only the selected Samba shares"
grep -Fq '"Samba shares $samba_names did not close; refusing to unmount disks' \
  "$repo_root/pi/scripts/umount_disks.sh" &&
  fail "disk-unmount alert still uses the old generic Samba wording"
grep -Fq 'ud_record_failure "Samba shares $samba_names did not close; disks remain mounted"' \
  "$repo_root/pi/scripts/umount_disks.sh" ||
  fail "disk-unmount fallback alert omits the affected Samba share names"
grep -Fq 'first_holders=$(ud_mount_holder_summary "$expected_mount")' \
  "$repo_root/pi/scripts/umount_disks.sh" ||
  fail "failed unmount does not collect bounded userspace-holder details"
grep -Fq 'ud_emergency_evict_mount_holders "$expected_mount"' \
  "$repo_root/pi/scripts/umount_disks.sh" ||
  fail "ignition emergency does not evict exact mount holders"
if grep -Fq '/usr/sbin/service smbd stop' "$repo_root/pi/scripts/umount_disks.sh"; then
  fail "disk unmount still stops global Samba"
fi
if grep -Eq 'pgrep[[:space:]]+-fi|pk[[:space:]]+qbit' "$repo_root/pi/scripts/remount.sh"; then
  fail "manual remount still uses a fuzzy qBittorrent process killer"
fi

echo "PASS: Samba share control is label-scoped and fail-closed"
