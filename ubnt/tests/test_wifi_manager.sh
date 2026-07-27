#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
manager="$script_dir/../persistent/scripts/wifi_manager.sh"
parser="$script_dir/../persistent/scripts/parse-iwlist.awk"
test_root=$(mktemp -d "${TMPDIR:-/tmp}/ubnt-manager-test.XXXXXX")
trap 'rm -rf "$test_root"' EXIT HUP INT TERM

mkdir -p "$test_root/bin" "$test_root/config" "$test_root/profiles" "$test_root/state"
for command_name in iwlist iwgetid mca-status ip ping softrestart cfgmtd; do
    ln -s "$script_dir/mock_command.sh" "$test_root/bin/$command_name"
done
ln -s "$script_dir/../persistent/scripts/ensure_ssh_keys.sh" \
    "$test_root/bin/ensure_ssh_keys"

printf '%s\n' 'admin-key' > "$test_root/authorized_keys"
printf '%s\n' 'pi-rsa-key' 'pi-ed25519-key' > "$test_root/persistent_keys"

profile="$test_root/profiles/A Network With Spaces"
printf '%s\n' \
    'wireless.1.ssid=A Network With Spaces' \
    'wireless.1.scan_list.status=disabled' \
    'wireless.1.scan_list.channels=' \
    'wpasupplicant.status=disabled' \
    'wpasupplicant.device.1.status=disabled' > "$profile"
printf '%s\n' \
    'wireless.1.ssid=missing-target' \
    'wireless.1.scan_list.status=disabled' \
    'wireless.1.scan_list.channels=' \
    'wpasupplicant.status=disabled' \
    'wpasupplicant.device.1.status=disabled' > "$test_root/profiles/missing-target"
printf '%s\n' \
    'wireless.1.ssid=template-network' \
    'wireless.1.ap=00:00:00:00:00:01' \
    'wireless.1.security.type=none' \
    'wireless.1.scan_list.status=disabled' \
    'wireless.1.scan_list.channels=' \
    'wpasupplicant.status=enabled' \
    'wpasupplicant.device.1.status=enabled' \
    'wpasupplicant.profile.1.network.1.ssid=template-network' \
    'wpasupplicant.profile.1.network.1.bssid=00:00:00:00:00:01' \
    'wpasupplicant.profile.1.network.1.psk=template-secret' > "$test_root/profiles/WPA Template"
profile_hash_before=$(md5 -q "$profile" 2>/dev/null || md5sum "$profile" | awk '{print $1}')
cp "$profile" "$test_root/system.cfg"
printf 'old-network\n' > "$test_root/associated"

export MOCK_FIXTURE="$script_dir/fixtures/iwlist-scan.txt"
export MOCK_FIXTURE_FIRST="$test_root/empty-first-scan"
export MOCK_IWLIST_COUNT_FILE="$test_root/iwlist-count"
export UBNT_SCAN_PASSES=3
export UBNT_SCAN_SETTLE_SECONDS=0
: > "$MOCK_FIXTURE_FIRST"
export MOCK_ASSOCIATED="$test_root/associated"
export UBNT_PROFILE_DIR="$test_root/profiles"
export UBNT_CONFIG_DIR="$test_root/config"
export UBNT_STATE_DIR="$test_root/state"
export UBNT_LOG_FILE="$test_root/wifi.log"
export UBNT_SYSTEM_CFG="$test_root/system.cfg"
export UBNT_SCAN_PARSER="$parser"
export UBNT_IWLIST="$test_root/bin/iwlist"
export UBNT_IWGETID="$test_root/bin/iwgetid"
export UBNT_MCA_STATUS="$test_root/bin/mca-status"
export UBNT_IP_CMD="$test_root/bin/ip"
export UBNT_PING="$test_root/bin/ping"
export UBNT_SOFTRESTART="$test_root/bin/softrestart"
export UBNT_CFGMTD="$test_root/bin/cfgmtd"
export UBNT_SSH_KEY_INSTALLER="$test_root/bin/ensure_ssh_keys"
export UBNT_SSH_KEY_SOURCE="$test_root/persistent_keys"
export UBNT_AUTHORIZED_KEYS="$test_root/authorized_keys"

"$manager" connect 'A Network With Spaces' >/dev/null
[ "$(sed -n '1p' "$MOCK_IWLIST_COUNT_FILE")" -eq 3 ]
! grep -q 'sensitive-test-value' "$test_root/wifi.log"
grep -q '^wireless.1.scan_list.status=enabled$' "$test_root/system.cfg"
grep -q '^wireless.1.scan_list.channels=2437$' "$test_root/system.cfg"
[ "$(sed -n '1p' "$test_root/associated")" = 'A Network With Spaces' ]
grep -qx 'admin-key' "$test_root/authorized_keys"
grep -qx 'pi-rsa-key' "$test_root/authorized_keys"
grep -qx 'pi-ed25519-key' "$test_root/authorized_keys"
[ "$(wc -l < "$test_root/authorized_keys" | tr -d '[:space:]')" -eq 3 ]
profile_hash_after=$(md5 -q "$profile" 2>/dev/null || md5sum "$profile" | awk '{print $1}')
[ "$profile_hash_before" = "$profile_hash_after" ]

"$manager" connect 'A Network With Spaces' >/dev/null
grep -q 'requested profile already ready profile=A Network With Spaces' "$test_root/wifi.log"

"$manager" auto >/dev/null
grep -q 'current connection healthy ssid=A Network With Spaces' "$test_root/wifi.log"
healthy_lines_before=$(wc -l < "$test_root/wifi.log")
"$manager" auto >/dev/null
healthy_lines_after=$(wc -l < "$test_root/wifi.log")
[ "$healthy_lines_before" -eq "$healthy_lines_after" ]

printf '%s\n' \
    'wireless.1.ssid=manual-target' \
    'wpasupplicant.status=disabled' \
    'wpasupplicant.device.1.status=disabled' > "$test_root/system.cfg"
printf 'old-network\n' > "$test_root/associated"
rm -f "$test_root/state/transition_started"
"$manager" auto >/dev/null
grep -q 'manual/config transition protected target=manual-target' "$test_root/wifi.log"

export UBNT_MAX_LOG_BYTES=100
export UBNT_LOG_KEEP_LINES=2
: > "$test_root/wifi.log"
rotation_line=1
while [ "$rotation_line" -le 20 ]; do
    printf 'old runtime log line %s\n' "$rotation_line" >> "$test_root/wifi.log"
    rotation_line=$((rotation_line + 1))
done
"$manager" pause >/dev/null
[ "$(wc -l < "$test_root/wifi.log")" -eq 3 ]
grep -q 'automatic selection paused' "$test_root/wifi.log"

unset UBNT_MAX_LOG_BYTES UBNT_LOG_KEEP_LINES
rm -f "$test_root/state/paused" "$test_root/state/cooldown."*
printf 'old-network\n' > "$test_root/associated"
cp "$test_root/profiles/A Network With Spaces" "$test_root/system.cfg"
: > "$MOCK_IWLIST_COUNT_FILE"
export MOCK_FAIL_SSID=missing-target
export UBNT_ASSOCIATE_FALLBACK_SECONDS=0
export UBNT_MANUAL_GRACE_SECONDS=2
if "$manager" connect missing-target >/dev/null; then
    echo 'Unavailable requested profile unexpectedly succeeded.' >&2
    exit 1
fi
unset MOCK_FAIL_SSID UBNT_ASSOCIATE_FALLBACK_SECONDS UBNT_MANUAL_GRACE_SECONDS
[ "$(sed -n '1p' "$test_root/associated")" = 'A Network With Spaces' ]
grep -q 'manual switch protection expired; recovering best available saved network' "$test_root/wifi.log"
grep -q 'automatic recovery selected profile=A Network With Spaces' "$test_root/wifi.log"
grep -q 'automatic recovery completed profile=A Network With Spaces' "$test_root/wifi.log"

dashboard_output=$("$manager" dashboard-scan)
printf '%s\n' "$dashboard_output" | grep -q '^state|'
printf '%s\n' "$dashboard_output" | grep -q '^profile|'
printf '%s\n' "$dashboard_output" | grep -q '^network|'
! printf '%s\n' "$dashboard_output" | grep -q 'template-secret'

rm -f "$test_root/state/paused"
printf 'old-network\n' > "$test_root/associated"
printf 'A Network With Spaces\n' | "$manager" manual-connect-stdin >/dev/null
[ -f "$test_root/state/paused" ]
[ "$(sed -n '1p' "$test_root/associated")" = 'A Network With Spaces' ]

rm -f "$test_root/state/paused"
printf '%s\n' \
    'dendelion' \
    'wpa' \
    'D8:EC:5E:8D:6A:3A' \
    'new-test-password' | "$manager" provision-stdin >/dev/null
[ -f "$test_root/state/paused" ]
[ -f "$test_root/profiles/dendelion" ]
grep -q '^wpasupplicant.profile.1.network.1.ssid=dendelion$' "$test_root/profiles/dendelion"
grep -q '^wpasupplicant.profile.1.network.1.bssid=D8:EC:5E:8D:6A:3A$' "$test_root/profiles/dendelion"
grep -q '^wpasupplicant.profile.1.network.1.psk=new-test-password$' "$test_root/profiles/dendelion"
grep -q '^wireless.1.scan_list.status=disabled$' "$test_root/profiles/dendelion"
! grep -q 'new-test-password' "$test_root/wifi.log"
! find "$test_root/profiles" -maxdepth 1 -name '.dashboard-new.*' | grep -q .

printf 'wifi-manager: ok\n'
