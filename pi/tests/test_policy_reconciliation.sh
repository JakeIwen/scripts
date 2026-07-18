#!/bin/bash

set -u

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
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
chmod +x "$test_root/policyctl" "$test_root/tuya-status"

export ISW_POLICYCTL="$test_root/policyctl"
export ISW_IGNITION_FLAG="$test_root/ignition_is_on"
export ISW_TUYA_STATUS="$test_root/tuya-status"
export TEST_STARLINK_CALLS="$test_root/starlink-calls"

# shellcheck source=../scripts/internet_switches.sh
source "$repo_root/pi/scripts/internet_switches.sh"

ACTION=""
IO_ERROR=1
mount_drives() { ACTION="${ACTION:+$ACTION }mount"; }
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
assert_eq "kill-all" "$ACTION" "ignition must override unreadable requested policy"
rm "$ISW_IGNITION_FLAG"

run_case "disabled disks" "0 1 1" off 0 "kill-all"
run_case "globally disabled torrents" "1 0 1" off 0 "mount kill-torrent"
run_case "explicit Starlink permission" "1 1 1" unknown 1 "mount start-torrent"
[[ ! -e "$TEST_STARLINK_CALLS" ]] ||
  fail "explicit Starlink permission should not query Starlink power"
run_case "Starlink blocked" "1 1 0" on 0 "mount kill-torrent"
run_case "non-Starlink torrenting" "1 1 0" off 0 "mount start-torrent"
run_case "unknown Starlink fails closed" "1 1 0" unknown 1 "mount kill-torrent"

export TEST_COMPACT_POLICY="1 1 0"
export TEST_STARLINK_STATE=off
export TEST_STARLINK_STATUS=0
ACTION=""
IO_ERROR=0
rm -f "$TEST_STARLINK_CALLS"
set_isw_options >/dev/null 2>&1 || fail "I/O-error policy returned failure"
assert_eq "mount kill-torrent" "$ACTION" "I/O error must stop torrents"
[[ ! -e "$TEST_STARLINK_CALLS" ]] || fail "I/O error should skip Starlink query"

export TEST_COMPACT_POLICY="1 maybe 0"
ACTION=""
if set_isw_options >/dev/null 2>&1; then
  fail "invalid compact policy was accepted"
fi
assert_eq "" "$ACTION" "invalid requested policy must not change runtime state"

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
