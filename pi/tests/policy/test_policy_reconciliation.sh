#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_eq() {
  local expected=$1 actual=$2 description=$3
  [[ "$actual" == "$expected" ]] ||
    fail "$description (expected '$expected', got '$actual')"
}

cat > "$test_root/policyctl" <<'HELPER'
#!/bin/bash
printf '%s\n' "$TEST_COMPACT_POLICY"
HELPER
cat > "$test_root/tuya-status" <<'HELPER'
#!/bin/bash
printf 'called\n' >> "$TEST_STARLINK_CALLS"
printf '%s\n' "$TEST_STARLINK_STATE"
exit "$TEST_STARLINK_STATUS"
HELPER
cat > "$test_root/pgrep" <<'HELPER'
#!/bin/bash
[[ "$#" == 2 && "$1" == -x && "$2" == qbittorrent-nox ]] || exit 99
printf '%s\n' "$*" >> "$TEST_PGREP_CALLS"
count=0
[[ ! -f "$TEST_PGREP_COUNT" ]] || read -r count < "$TEST_PGREP_COUNT"
count=$((count + 1))
printf '%s\n' "$count" > "$TEST_PGREP_COUNT"
(( count <= TEST_PGREP_RUNNING_CALLS ))
HELPER
cat > "$test_root/pkill" <<'HELPER'
#!/bin/bash
[[ "$#" == 3 && "$1" == -TERM && "$2" == -x && "$3" == qbittorrent-nox ]] || exit 99
printf '%s\n' "$*" >> "$TEST_PKILL_CALLS"
exit "${TEST_PKILL_STATUS:-0}"
HELPER
cat > "$test_root/sleep" <<'HELPER'
#!/bin/bash
[[ "$#" == 1 && "$1" == 1 ]] || exit 99
printf '%s\n' "$1" >> "$TEST_SLEEP_CALLS"
HELPER
cat > "$test_root/mount-disks" <<'HELPER'
#!/bin/bash
printf 'mount:%s\n' "${1:-default}" >> "$TEST_STALE_RECOVERY_CALLS"
case "${1:-}" in
  --list-stale)
    printf '%s' "${TEST_STALE_MOUNTS:-}"
    exit "${TEST_STALE_SCAN_STATUS:-0}"
    ;;
  --recover-stale)
    exit "${TEST_STALE_RECOVERY_STATUS:-0}"
    ;;
  *) exit 0 ;;
esac
HELPER
cat > "$test_root/abort-backup" <<'HELPER'
#!/bin/bash
printf 'abort-backup\n' >> "$TEST_STALE_RECOVERY_CALLS"
exit "${TEST_ABORT_BACKUP_STATUS:-0}"
HELPER
cat > "$test_root/samba-share-control" <<'HELPER'
#!/bin/bash
printf 'samba:%s\n' "$*" >> "$TEST_STALE_RECOVERY_CALLS"
exit "${TEST_SAMBA_CONTROL_STATUS:-0}"
HELPER
chmod +x "$test_root/policyctl" "$test_root/tuya-status" \
  "$test_root/pgrep" "$test_root/pkill" "$test_root/sleep" \
  "$test_root/mount-disks" "$test_root/abort-backup" \
  "$test_root/samba-share-control"

export ISW_POLICYCTL="$test_root/policyctl"
export ISW_IGNITION_FLAG="$test_root/ignition_is_on"
export ISW_TUYA_STATUS="$test_root/tuya-status"
export ISW_PGREP="$test_root/pgrep"
export ISW_PKILL="$test_root/pkill"
export ISW_SLEEP="$test_root/sleep"
export ISW_MOUNT_DISKS="$test_root/mount-disks"
export ISW_ABORT_BACKUP="$test_root/abort-backup"
export ISW_SAMBA_SHARE_CONTROL="$test_root/samba-share-control"
export TEST_STARLINK_CALLS="$test_root/starlink-calls"
export TEST_PGREP_CALLS="$test_root/pgrep-calls"
export TEST_PGREP_COUNT="$test_root/pgrep-count"
export TEST_PKILL_CALLS="$test_root/pkill-calls"
export TEST_SLEEP_CALLS="$test_root/sleep-calls"
export TEST_STALE_RECOVERY_CALLS="$test_root/stale-recovery-calls"

# shellcheck source=../../scripts/internet_switches.sh
source "$repo_root/pi/scripts/internet_switches.sh"

reset_process_test() {
  export TEST_PGREP_RUNNING_CALLS=$1
  export TEST_PKILL_STATUS=${2:-0}
  rm -f "$TEST_PGREP_CALLS" "$TEST_PGREP_COUNT" \
    "$TEST_PKILL_CALLS" "$TEST_SLEEP_CALLS"
}

reset_process_test 0
kill_torrent_client >/dev/null 2>&1 || fail "absent torrent client was a stop failure"
[[ ! -e "$TEST_PKILL_CALLS" ]] || fail "absent torrent client was signaled"
assert_eq "-x qbittorrent-nox" "$(cat "$TEST_PGREP_CALLS")" \
  "torrent discovery must use exact process identity"

reset_process_test 1
kill_torrent_client >/dev/null 2>&1 || fail "graceful torrent stop returned failure"
assert_eq "-TERM -x qbittorrent-nox" "$(cat "$TEST_PKILL_CALLS")" \
  "torrent stop must signal only the exact process identity"
assert_eq 2 "$(cat "$TEST_PGREP_COUNT")" \
  "graceful torrent stop must verify process exit"

reset_process_test 31
if kill_torrent_client >/dev/null 2>&1; then
  fail "stuck torrent client was reported stopped"
fi
assert_eq 30 "$(wc -l < "$TEST_SLEEP_CALLS" | tr -d ' ')" \
  "stuck torrent stop must be bounded at 30 seconds"

kill_torrent_client() {
  printf 'kill-torrent\n' >> "$TEST_STALE_RECOVERY_CALLS"
}

: > "$TEST_STALE_RECOVERY_CALLS"
export TEST_STALE_MOUNTS=""
recover_stale_mounts_if_needed >/dev/null 2>&1 ||
  fail "empty stale-mount scan returned failure"
assert_eq "mount:--list-stale" "$(cat "$TEST_STALE_RECOVERY_CALLS")" \
  "empty stale scan must not stop consumers"

: > "$TEST_STALE_RECOVERY_CALLS"
export TEST_STALE_MOUNTS=$'EXFAT512\t/mnt/EXFAT512\t/dev/vanished'
recover_stale_mounts_if_needed >/dev/null 2>&1 ||
  fail "stale-mount recovery orchestration returned failure"
expected_calls=$'mount:--list-stale\nabort-backup\nsamba:close EXFAT512\nmount:--recover-stale'
assert_eq "$expected_calls" "$(cat "$TEST_STALE_RECOVERY_CALLS")" \
  "stale flash recovery must leave unrelated torrents and Samba shares online"

: > "$TEST_STALE_RECOVERY_CALLS"
export TEST_STALE_MOUNTS=$'movingparts\t/mnt/movingparts\t/dev/vanished'
recover_stale_mounts_if_needed >/dev/null 2>&1 ||
  fail "stale movingparts recovery returned failure"
expected_calls=$'mount:--list-stale\nabort-backup\nkill-torrent\nsamba:close movingparts\nmount:--recover-stale'
assert_eq "$expected_calls" "$(cat "$TEST_STALE_RECOVERY_CALLS")" \
  "stale media recovery must stop only its actual consumers"

: > "$TEST_STALE_RECOVERY_CALLS"
export TEST_SAMBA_CONTROL_STATUS=1
if recover_stale_mounts_if_needed >/dev/null 2>&1; then
  fail "stale recovery ignored Samba close failure"
fi
[[ $(cat "$TEST_STALE_RECOVERY_CALLS") != *"mount:--recover-stale"* ]] ||
  fail "stale recovery unmounted after Samba close failed"
unset TEST_SAMBA_CONTROL_STATUS

ACTION=""
kill_torrent_client() { ACTION="${ACTION:+$ACTION }kill-torrent"; }
stop_service() { ACTION="${ACTION:+$ACTION }stop:$1"; }
unmount_drives() { ACTION="${ACTION:+$ACTION }unmount"; }

kill_all >/dev/null 2>&1 || fail "all-HDD shutdown returned failure"
assert_eq "kill-torrent stop:smbd unmount" "$ACTION" \
  "all-HDD shutdown must stop global Samba before unmounting"

ACTION=""
touch "$ISW_IGNITION_FLAG"
if mount_drives >/dev/null 2>&1; then
  fail "ignition-interrupted mount unexpectedly succeeded"
fi
rm "$ISW_IGNITION_FLAG"
assert_eq "kill-torrent stop:smbd unmount" "$ACTION" \
  "ignition-interrupted mount must stop global Samba before unmounting"

ACTION=""
IO_ERROR=1
recover_stale_mounts_if_needed() { return 0; }
mount_drives() { ACTION="${ACTION:+$ACTION }mount"; }
mount_always_available_drives() {
  ACTION="${ACTION:+$ACTION }mount-always"
  return "${TEST_ALWAYS_MOUNT_STATUS:-0}"
}
kill_torrent_client() { ACTION="${ACTION:+$ACTION }kill-torrent"; }
start_torrent_client() { ACTION="${ACTION:+$ACTION }start-torrent"; }
kill_all() { ACTION="${ACTION:+$ACTION }kill-all"; }
has_io_error() { return "$IO_ERROR"; }

run_case() {
  local description=$1 policy=$2 starlink_state=$3 starlink_status=$4 expected=$5
  export TEST_COMPACT_POLICY=$policy
  export TEST_STARLINK_STATE=$starlink_state
  export TEST_STARLINK_STATUS=$starlink_status
  ACTION=""
  IO_ERROR=1
  rm -f "$TEST_STARLINK_CALLS"
  set_isw_options >/dev/null 2>&1 || fail "$description returned failure"
  assert_eq "$expected" "$ACTION" "$description"
}

touch "$ISW_IGNITION_FLAG"
export TEST_COMPACT_POLICY="invalid"
ACTION=""
set_isw_options >/dev/null 2>&1 || fail "ignition override returned failure"
assert_eq "kill-all mount-always" "$ACTION" \
  "ignition must spin down HDDs and reconcile flash before reading requested policy"
rm "$ISW_IGNITION_FLAG"

run_case "disabled disks" "0 1 1" off 0 "mount-always kill-all"
run_case "globally disabled torrents" "1 0 1" off 0 "mount-always mount kill-torrent"
run_case "explicit Starlink permission" "1 1 1" unknown 1 "mount-always mount start-torrent"
[[ ! -e "$TEST_STARLINK_CALLS" ]] ||
  fail "explicit Starlink permission should not query Starlink power"
run_case "Starlink blocked" "1 1 0" on 0 "mount-always mount kill-torrent"
run_case "non-Starlink torrenting" "1 1 0" off 0 "mount-always mount start-torrent"
run_case "unknown Starlink fails closed" "1 1 0" unknown 1 "mount-always mount kill-torrent"

export TEST_COMPACT_POLICY="1 1 0"
export TEST_STARLINK_STATE=off
export TEST_STARLINK_STATUS=0
ACTION=""
IO_ERROR=0
rm -f "$TEST_STARLINK_CALLS"
set_isw_options >/dev/null 2>&1 || fail "I/O-error policy returned failure"
assert_eq "mount-always mount kill-torrent" "$ACTION" "I/O error must stop torrents"
[[ ! -e "$TEST_STARLINK_CALLS" ]] || fail "I/O error should skip Starlink query"

export TEST_COMPACT_POLICY="1 maybe 0"
ACTION=""
if set_isw_options >/dev/null 2>&1; then
  fail "invalid compact policy was accepted"
fi
assert_eq "mount-always" "$ACTION" \
  "invalid HDD policy must not block independent flash reconciliation"

export TEST_ALWAYS_MOUNT_STATUS=1
export TEST_COMPACT_POLICY="0 1 0"
ACTION=""
if set_isw_options >/dev/null 2>&1; then
  fail "always-mount failure was hidden"
fi
assert_eq "mount-always kill-all" "$ACTION" \
  "flash failure must not prevent disabled HDD policy from reaching kill-all"
unset TEST_ALWAYS_MOUNT_STATUS

hook_home="$test_root/hook-home"
mkdir -p "$hook_home/scripts" "$hook_home/hooks/inactive" \
  "$hook_home/log" "$hook_home/.config/vanpi"
printf '{"sentinel":true}\n' > "$hook_home/.config/vanpi/policy.json"
for helper in tuya_toggle.sh tuya_device_ids.sh cop_alert_ext_flood_guard.sh; do
  printf '#!/bin/bash\nexit 0\n' > "$hook_home/scripts/$helper"
  chmod +x "$hook_home/scripts/$helper"
done
printf '#!/bin/bash\nprintf "run\\n" >> "$HOME/policy_calls"\nsleep 1\n' \
  > "$hook_home/scripts/internet_switches.sh"
chmod +x "$hook_home/scripts/internet_switches.sh"

policy_before=$(cat "$hook_home/.config/vanpi/policy.json")
HOME="$hook_home" bash "$repo_root/pi/hooks/ignition_on.sh" >/dev/null ||
  fail "ignition-on hook returned failure"
HOME="$hook_home" bash "$repo_root/pi/hooks/ignition_off.sh" >/dev/null ||
  fail "ignition-off hook returned failure"
assert_eq "$policy_before" "$(cat "$hook_home/.config/vanpi/policy.json")" \
  "ignition hooks must not modify requested state"
[[ ! -e "$hook_home/mconf_last" ]] || fail "ignition hooks created mconf_last"
assert_eq 2 "$(wc -l < "$hook_home/policy_calls" | tr -d ' ')" \
  "both ignition transitions must reconcile policy"

echo "PASS: storage and torrent policy reconciliation"
