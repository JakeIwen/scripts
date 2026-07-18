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

export ISW_MCONF_DIR="$test_root/mconf"
export ISW_IGNITION_FLAG="$test_root/ignition_is_on"
mkdir -p "$ISW_MCONF_DIR"

# shellcheck source=../scripts/internet_switches.sh
source "$repo_root/pi/scripts/internet_switches.sh"

ACTION=""
kill_all() { ACTION=kill_all; }
mobile_internet_ops() { ACTION=mobile_internet_ops; }
lifi_internet_ops() { ACTION=lifi_internet_ops; }
ubnt_internet_ops() { ACTION=ubnt_internet_ops; }
no_internet_ops() { ACTION=no_internet_ops; }
refresh_mwan_state() {
  ISW_MWAN_STATE=$'Interface status:\n interface clientwan is online'
}

touch "$ISW_MCONF_DIR/idisk" "$ISW_MCONF_DIR/mdisk" "$ISW_MCONF_DIR/mtorrent"
requested_before=$(ls -1 "$ISW_MCONF_DIR" | sort)
touch "$ISW_IGNITION_FLAG"
set_isw_options >/dev/null || fail "ignition-on policy returned failure"
assert_eq kill_all "$ACTION" "ignition must override requested disk state"
assert_eq "$requested_before" "$(ls -1 "$ISW_MCONF_DIR" | sort)" \
  "policy must not modify requested state"

rm "$ISW_IGNITION_FLAG" "$ISW_MCONF_DIR/idisk" \
  "$ISW_MCONF_DIR/mdisk" "$ISW_MCONF_DIR/mtorrent"
touch "$ISW_MCONF_DIR/nodisk"
ACTION=""
set_isw_options >/dev/null || fail "nodisk policy returned failure"
assert_eq kill_all "$ACTION" "nodisk must disable disks while parked"

rm "$ISW_MCONF_DIR/nodisk"
touch "$ISW_MCONF_DIR/mdisk"
ACTION=""
set_isw_options >/dev/null || fail "parked requested policy returned failure"
assert_eq mobile_internet_ops "$ACTION" \
  "parked policy must apply the latest requested state"

hook_home="$test_root/hook-home"
mkdir -p "$hook_home/scripts" "$hook_home/hooks/inactive" \
  "$hook_home/log" "$hook_home/mconf"
touch "$hook_home/mconf/mdisk" "$hook_home/mconf/mtorrent"

for helper in tuya_toggle.sh tuya_device_ids.sh cop_alert_ext_flood_guard.sh; do
  printf '#!/bin/bash\nexit 0\n' > "$hook_home/scripts/$helper"
  chmod +x "$hook_home/scripts/$helper"
done
printf '#!/bin/bash\nprintf "run\\n" >> "$HOME/policy_calls"\nsleep 1\n' \
  > "$hook_home/scripts/internet_switches.sh"
chmod +x "$hook_home/scripts/internet_switches.sh"

hook_requested_before=$(ls -1 "$hook_home/mconf" | sort)
HOME="$hook_home" bash "$repo_root/pi/hooks/ignition_on.sh" >/dev/null ||
  fail "ignition-on hook returned failure"
HOME="$hook_home" bash "$repo_root/pi/hooks/ignition_off.sh" >/dev/null ||
  fail "ignition-off hook returned failure"
assert_eq "$hook_requested_before" "$(ls -1 "$hook_home/mconf" | sort)" \
  "ignition hooks must not modify requested state"
[[ ! -e "$hook_home/mconf_last" ]] || fail "ignition hooks created mconf_last"
assert_eq 2 "$(wc -l < "$hook_home/policy_calls" | tr -d ' ')" \
  "both ignition transitions must reconcile policy"

echo "PASS: ignition policy keeps requested state separate"
