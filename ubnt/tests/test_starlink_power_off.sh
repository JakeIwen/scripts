#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
manager="$script_dir/../persistent/scripts/wifi_manager.sh"
test_root=$(mktemp -d "${TMPDIR:-/tmp}/ubnt-starlink-off-test.XXXXXX")
trap 'rm -rf "$test_root"' EXIT HUP INT TERM
mkdir -p "$test_root/bin" "$test_root/profiles" "$test_root/config" "$test_root/state"
for command_name in iwlist iwgetid mca-status ip ping softrestart cfgmtd; do
    ln -s "$script_dir/mock_command.sh" "$test_root/bin/$command_name"
done
export UBNT_PROFILE_DIR="$test_root/profiles" UBNT_CONFIG_DIR="$test_root/config"
export UBNT_STATE_DIR="$test_root/state" UBNT_SYSTEM_CFG="$test_root/system.cfg"
export UBNT_LOG_FILE="$test_root/manager.log" UBNT_UPTIME_FILE="$test_root/uptime"
export UBNT_SCAN_PARSER="$script_dir/../persistent/scripts/parse-iwlist.awk"
export UBNT_IWLIST="$test_root/bin/iwlist" UBNT_IWGETID="$test_root/bin/iwgetid"
export UBNT_MCA_STATUS="$test_root/bin/mca-status" UBNT_IP_CMD="$test_root/bin/ip"
export UBNT_PING="$test_root/bin/ping" UBNT_SOFTRESTART="$test_root/bin/softrestart"
export UBNT_CFGMTD="$test_root/bin/cfgmtd"
export UBNT_SSH_KEY_INSTALLER="$script_dir/../persistent/scripts/ensure_ssh_keys.sh"
export UBNT_SSH_KEY_SOURCE="$test_root/no-persistent-keys"
export UBNT_MD5SUM=$(command -v md5sum)
export UBNT_SCAN_SETTLE_SECONDS=0
export MOCK_FIXTURE="$script_dir/fixtures/iwlist-scan.txt"
export MOCK_ASSOCIATED="$test_root/associated" MOCK_RELOAD_CHANNELS="$test_root/reloads"
export MOCK_IWLIST_COUNT_FILE="$test_root/scans"
printf '1000.0 0.0\n' > "$UBNT_UPTIME_FILE"
printf '%s\n' 'users.1.name=ubnt' 'users.1.password=admin-test-hash' \
    'wireless.1.ssid=denlink' 'wireless.1.ap=4E:EA:85:26:34:F4' \
    'wpasupplicant.status=enabled' 'wpasupplicant.device.1.status=enabled' \
    'wpasupplicant.profile.1.network.1.ssid=denlink' \
    'wpasupplicant.profile.1.network.1.psk=wifi-test-password' \
    > "$UBNT_PROFILE_DIR/denlink"
printf '%s\n' 'users.1.name=ubnt' 'users.1.password=admin-test-hash' \
    'wireless.1.ssid=A Network With Spaces' 'wpasupplicant.status=disabled' \
    'wpasupplicant.device.1.status=disabled' > "$UBNT_PROFILE_DIR/Other"
before=$(md5sum "$UBNT_PROFILE_DIR/denlink" | awk '{print $1}')
: > "$UBNT_CONFIG_DIR/prefer_denlink"

reset_denlink() {
    cp "$UBNT_PROFILE_DIR/denlink" "$UBNT_SYSTEM_CFG"
    printf 'denlink\n' > "$MOCK_ASSOCIATED"
    printf '1000\ndenlink\n' > "$UBNT_STATE_DIR/manual-hold"
    printf '1000\n' > "$UBNT_STATE_DIR/transition_started"
    : > "$MOCK_RELOAD_CHANNELS"
    : > "$MOCK_IWLIST_COUNT_FILE"
}

# Leave denlink, clear its grace/portal protection, and prefer an alternative
# even though denlink is still visible, stronger, and explicitly preferred.
reset_denlink
if ! "$manager" starlink-off >/dev/null; then
    cat "$UBNT_LOG_FILE" >&2
    exit 1
fi
[ "$(cat "$MOCK_ASSOCIATED")" = 'A Network With Spaces' ]
[ "$(wc -l < "$MOCK_RELOAD_CHANNELS" | tr -d '[:space:]')" -eq 2 ]
[ "$(cat "$MOCK_IWLIST_COUNT_FILE")" = 3 ]
[ ! -f "$UBNT_STATE_DIR/manual-hold" ]
[ ! -f "$UBNT_STATE_DIR/transition_started" ]
[ "$(cat "$UBNT_STATE_DIR/cooldown.denlink")" = 1120 ]
[ "$(md5sum "$UBNT_PROFILE_DIR/denlink" | awk '{print $1}')" = "$before" ]
grep -qx 'users.1.password=admin-test-hash' "$UBNT_SYSTEM_CFG"

# Power-off is a no-op on unrelated Wi-Fi, including its manual protection.
printf '1000\nA Network With Spaces\n' > "$UBNT_STATE_DIR/manual-hold"
: > "$UBNT_STATE_DIR/paused"
: > "$MOCK_RELOAD_CHANNELS"
: > "$MOCK_IWLIST_COUNT_FILE"
current=$(md5sum "$UBNT_SYSTEM_CFG" | awk '{print $1}')
"$manager" starlink-off >/dev/null
[ "$(md5sum "$UBNT_SYSTEM_CFG" | awk '{print $1}')" = "$current" ]
[ -f "$UBNT_STATE_DIR/manual-hold" ]
[ -f "$UBNT_STATE_DIR/paused" ]
[ ! -s "$MOCK_RELOAD_CHANNELS" ]
[ ! -s "$MOCK_IWLIST_COUNT_FILE" ]

# An explicit maintenance pause survives even when denlink must be released.
reset_denlink
"$manager" starlink-off >/dev/null
[ -f "$UBNT_STATE_DIR/paused" ]
[ ! -s "$MOCK_IWLIST_COUNT_FILE" ]
[ "$(cat "$MOCK_ASSOCIATED")" != denlink ]
rm "$UBNT_STATE_DIR/paused"

# With nothing else visible, remain disconnected and let the next auto pass
# scan again immediately, without inventing a new 120-second transition grace.
reset_denlink
: > "$test_root/empty-scan"
export MOCK_FIXTURE="$test_root/empty-scan"
"$manager" starlink-off >/dev/null
[ "$(cat "$MOCK_ASSOCIATED")" != denlink ]
[ "$(cat "$MOCK_IWLIST_COUNT_FILE")" = 3 ]
export MOCK_CCQ=0
"$manager" auto >/dev/null
[ "$(cat "$MOCK_IWLIST_COUNT_FILE")" = 6 ]
[ ! -f "$UBNT_STATE_DIR/transition_started" ]
[ "$(md5sum "$UBNT_PROFILE_DIR/denlink" | awk '{print $1}')" = "$before" ]
! grep -qE 'admin-test-hash|wifi-test-password' "$UBNT_LOG_FILE"
printf 'starlink-power-off: ok\n'
