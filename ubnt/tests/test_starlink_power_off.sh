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
export MOCK_RADIO_EVENTS="$test_root/radio-events"
standard_scan_frequencies='2412, 2417, 2422, 2427, 2432, 2437, 2442, 2447, 2452, 2457, 2462'
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
    : > "$MOCK_RADIO_EVENTS"
    rm -f "$UBNT_STATE_DIR/scan-completed" "$UBNT_STATE_DIR/scan.results" \
        "$UBNT_STATE_DIR/cooldown.Other"
}

cached_full_scan() {
    "$manager" dashboard-scan >/dev/null
    # Model a connection pin applied after the completed full survey.
    cp "$UBNT_PROFILE_DIR/denlink" "$UBNT_SYSTEM_CFG"
    printf '%s\n' 'wireless.1.scan_list.status=enabled' \
        'wireless.1.scan_list.channels=2462' >> "$UBNT_SYSTEM_CFG"
    : > "$MOCK_RELOAD_CHANNELS"
    : > "$MOCK_IWLIST_COUNT_FILE"
    : > "$MOCK_RADIO_EVENTS"
}

# The public pause command can run during either survey without taking the
# selector lock. Finish releasing denlink, but do not start another connection.
for pause_scan_channels in "$standard_scan_frequencies" 2462; do
    reset_denlink
    printf '%s\n' 'wireless.1.scan_list.status=enabled' \
        "wireless.1.scan_list.channels=$pause_scan_channels" >> "$UBNT_SYSTEM_CFG"
    export MOCK_PAUSE_MANAGER="$manager"
    "$manager" starlink-off >/dev/null
    [ -f "$UBNT_STATE_DIR/paused" ]
    case $(cat "$MOCK_ASSOCIATED") in
        vanpi-disconnected-*) ;;
        *) printf 'maintenance pause during survey allowed roaming\n' >&2; exit 1 ;;
    esac
    [ "$(cat "$MOCK_RELOAD_CHANNELS")" = "$standard_scan_frequencies" ]
    [ "$(cat "$MOCK_IWLIST_COUNT_FILE")" = 3 ]
    "$manager" resume >/dev/null
done
unset MOCK_PAUSE_MANAGER

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
[ "$(cat "$UBNT_STATE_DIR/cooldown.denlink")" -gt 1000 ]
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

# All-channel radio: survey while still configured for denlink, then apply
# the alternative directly. There is no intermediate disconnect reload.
unset MOCK_CCQ
export MOCK_FIXTURE="$script_dir/fixtures/iwlist-scan.txt"
reset_denlink
printf '%s\n' 'wireless.1.scan_list.status=enabled' \
    "wireless.1.scan_list.channels=$standard_scan_frequencies" >> "$UBNT_SYSTEM_CFG"
"$manager" starlink-off >/dev/null
[ "$(cat "$MOCK_ASSOCIATED")" = 'A Network With Spaces' ]
[ "$(cat "$MOCK_RELOAD_CHANNELS")" = 2437 ]
[ "$(cat "$MOCK_IWLIST_COUNT_FILE")" = 3 ]
[ "$(sed -n '1p' "$MOCK_RADIO_EVENTS")" = iwlist ]
[ ! -f "$UBNT_STATE_DIR/manual-hold" ]
[ ! -f "$UBNT_STATE_DIR/transition_started" ]

# A recent full survey works even when the live radio is pinned, without
# another survey or disconnect.
for scan_time in 1000; do
    reset_denlink
    cached_full_scan
    printf '%s\n' "$scan_time" > "$UBNT_STATE_DIR/scan-completed"
    "$manager" starlink-off >/dev/null
    [ "$(cat "$MOCK_ASSOCIATED")" = 'A Network With Spaces' ]
    [ "$(cat "$MOCK_RELOAD_CHANNELS")" = 2437 ]
    [ ! -s "$MOCK_IWLIST_COUNT_FILE" ]
    [ "$(cat "$UBNT_STATE_DIR/cooldown.denlink")" -gt 1000 ]
done

# Stale and empty cache entries need the original
# disconnect/full-survey path on a pinned radio.
for cache_case in stale empty; do
    reset_denlink
    cached_full_scan
    case $cache_case in
        stale) printf '699\n' > "$UBNT_STATE_DIR/scan-completed" ;;
        empty) : > "$UBNT_STATE_DIR/scan.results" ;;
    esac
    "$manager" starlink-off >/dev/null
    [ "$(wc -l < "$MOCK_RELOAD_CHANNELS" | tr -d '[:space:]')" -eq 2 ]
    [ "$(sed -n '1p' "$MOCK_RELOAD_CHANNELS")" = "$standard_scan_frequencies" ]
    [ "$(cat "$MOCK_IWLIST_COUNT_FILE")" = 3 ]
    [ "$(sed -n '1p' "$MOCK_RADIO_EVENTS")" = softrestart ]
done

# A stale candidate can disappear after the survey. Failed direct association
# must release denlink, cool down that candidate and run a fresh full survey.
reset_denlink
cached_full_scan
export MOCK_FAIL_SSID='A Network With Spaces'
export UBNT_ASSOCIATE_FAST_SECONDS=0 UBNT_ASSOCIATE_FALLBACK_SECONDS=0
"$manager" starlink-off >/dev/null
[ "$(wc -l < "$MOCK_RELOAD_CHANNELS" | tr -d '[:space:]')" -eq 3 ]
[ "$(cat "$MOCK_IWLIST_COUNT_FILE")" = 3 ]
case $(cat "$MOCK_ASSOCIATED") in vanpi-disconnected-*) ;; *) exit 1 ;; esac
[ -f "$UBNT_STATE_DIR/cooldown.Other" ]
[ ! -f "$UBNT_STATE_DIR/transition_started" ]
[ ! -f "$UBNT_STATE_DIR/manual-hold" ]
unset MOCK_FAIL_SSID UBNT_ASSOCIATE_FAST_SECONDS UBNT_ASSOCIATE_FALLBACK_SECONDS

# Recovery can select a different saved candidate after cooling down the miss.
reset_denlink
sed -e 's/denlink/dendelion/g' -e 's/4E:EA:85:26:34:F4/D8:EC:5E:8D:6A:3A/g' \
    "$UBNT_PROFILE_DIR/denlink" > "$UBNT_PROFILE_DIR/Backup"
cached_full_scan
export MOCK_FAIL_SSID='A Network With Spaces'
export UBNT_ASSOCIATE_FAST_SECONDS=0 UBNT_ASSOCIATE_FALLBACK_SECONDS=0
"$manager" starlink-off >/dev/null
[ "$(cat "$MOCK_ASSOCIATED")" = dendelion ]
[ "$(wc -l < "$MOCK_RELOAD_CHANNELS" | tr -d '[:space:]')" -eq 4 ]
[ "$(cat "$MOCK_IWLIST_COUNT_FILE")" = 3 ]
[ -f "$UBNT_STATE_DIR/cooldown.Other" ]
rm "$UBNT_PROFILE_DIR/Backup"
unset MOCK_FAIL_SSID UBNT_ASSOCIATE_FAST_SECONDS UBNT_ASSOCIATE_FALLBACK_SECONDS

# A targeted-channel miss retains the normal all-channel connection fallback.
reset_denlink
cached_full_scan
export MOCK_FAIL_FREQUENCY=2437 UBNT_ASSOCIATE_FAST_SECONDS=0
"$manager" starlink-off >/dev/null
[ "$(wc -l < "$MOCK_RELOAD_CHANNELS" | tr -d '[:space:]')" -eq 2 ]
[ "$(sed -n '2p' "$MOCK_RELOAD_CHANNELS")" = "$standard_scan_frequencies" ]
[ "$(cat "$MOCK_ASSOCIATED")" = 'A Network With Spaces' ]
[ ! -s "$MOCK_IWLIST_COUNT_FILE" ]
unset MOCK_FAIL_FREQUENCY UBNT_ASSOCIATE_FAST_SECONDS

# Association without working Internet is retained for portal login.
reset_denlink
cached_full_scan
export MOCK_PING_STATUS=1
"$manager" starlink-off >/dev/null
[ "$(cat "$MOCK_ASSOCIATED")" = 'A Network With Spaces' ]
[ "$(cat "$MOCK_RELOAD_CHANNELS")" = 2437 ]
[ ! -s "$MOCK_IWLIST_COUNT_FILE" ]
unset MOCK_PING_STATUS

# A maintenance pause takes precedence over even a fresh cached alternative.
reset_denlink
cached_full_scan
: > "$UBNT_STATE_DIR/paused"
"$manager" starlink-off >/dev/null
[ -f "$UBNT_STATE_DIR/paused" ]
[ "$(cat "$MOCK_RELOAD_CHANNELS")" = "$standard_scan_frequencies" ]
[ ! -s "$MOCK_IWLIST_COUNT_FILE" ]
[ "$(cat "$MOCK_ASSOCIATED")" != denlink ]
[ "$(md5sum "$UBNT_PROFILE_DIR/denlink" | awk '{print $1}')" = "$before" ]
grep -qx 'users.1.password=admin-test-hash' "$UBNT_SYSTEM_CFG"
! grep -qE 'admin-test-hash|wifi-test-password|sensitive-test-value' "$UBNT_LOG_FILE"
printf 'starlink-power-off: ok\n'
