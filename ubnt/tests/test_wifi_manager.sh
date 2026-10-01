#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
manager="$script_dir/../persistent/scripts/wifi_manager.sh"
parser="$script_dir/../persistent/scripts/parse-iwlist.awk"
standard_scan_frequencies='2412, 2417, 2422, 2427, 2432, 2437, 2442, 2447, 2452, 2457, 2462'
test_root=$(mktemp -d "${TMPDIR:-/tmp}/ubnt-manager-test.XXXXXX")
trap 'rm -rf "$test_root"' EXIT HUP INT TERM

mkdir -p "$test_root/bin" "$test_root/config" "$test_root/profiles" "$test_root/state"
export UBNT_UPTIME_FILE=/proc/uptime
if [ ! -r "$UBNT_UPTIME_FILE" ]; then
    export UBNT_UPTIME_FILE="$test_root/uptime"
    printf '100000.0 0.0\n' > "$UBNT_UPTIME_FILE"
fi
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
    'aaa.1.wpa.psk=template-secret' \
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
export UBNT_MD5SUM=$(command -v md5sum)
export UBNT_SSH_KEY_INSTALLER="$test_root/bin/ensure_ssh_keys"
export UBNT_SSH_KEY_SOURCE="$test_root/persistent_keys"
export UBNT_AUTHORIZED_KEYS="$test_root/authorized_keys"

"$manager" connect 'A Network With Spaces' >/dev/null
[ ! -s "$MOCK_IWLIST_COUNT_FILE" ]
! grep -q 'sensitive-test-value' "$test_root/wifi.log"
grep -Fqx 'wireless.1.scan_list.status=enabled' "$test_root/system.cfg"
grep -Fqx "wireless.1.scan_list.channels=$standard_scan_frequencies" "$test_root/system.cfg"
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
profile_hash_after_auto=$(md5 -q "$profile" 2>/dev/null || md5sum "$profile" | awk '{print $1}')
[ "$profile_hash_before" = "$profile_hash_after_auto" ]
healthy_lines_before=$(wc -l < "$test_root/wifi.log")
"$manager" auto >/dev/null
healthy_lines_after=$(wc -l < "$test_root/wifi.log")
[ "$healthy_lines_before" -eq "$healthy_lines_after" ]

# Automatic selection clears a stale live frequency restriction before its
# full scan, sees channels beyond the old pin, and can select another profile.
printf '%s\n' \
    'wireless.1.ssid=denlink' \
    'wireless.1.scan_list.status=enabled' \
    'wireless.1.scan_list.channels=2462' \
    'wpasupplicant.status=disabled' \
    'wpasupplicant.device.1.status=disabled' > "$test_root/system.cfg"
printf 'denlink\n' > "$test_root/associated"
"$UBNT_MD5SUM" "$test_root/system.cfg" | awk '{print $1}' > \
    "$test_root/state/observed-system-config.md5"
: > "$MOCK_IWLIST_COUNT_FILE"
rm -f "$test_root/state/last_auto_scan"
export UBNT_AUTO_SCAN_INTERVAL=0
"$manager" auto >/dev/null
unset UBNT_AUTO_SCAN_INTERVAL
grep -Fqx 'wireless.1.scan_list.status=enabled' "$test_root/system.cfg"
grep -Fqx 'wireless.1.scan_list.channels=2437' "$test_root/system.cfg"
grep -q 'applying standard scan-frequency allowlist reason=full-scan' "$test_root/wifi.log"
[ "$(sed -n '1p' "$MOCK_IWLIST_COUNT_FILE")" -eq 3 ]
grep -q '|2412|1|' "$test_root/state/scan.results"
grep -q '|2462|11|' "$test_root/state/scan.results"
[ "$(sed -n '1p' "$test_root/associated")" = 'A Network With Spaces' ]

# A native airOS GUI Apply is detected from the system configuration digest.
# While it is stabilizing, the old association must not trigger a roaming scan.
printf '%s\n' \
    'wireless.1.ssid=manual-target' \
    'wireless.1.scan_list.status=enabled' \
    'wireless.1.scan_list.channels=2462' \
    'wpasupplicant.status=disabled' \
    'wpasupplicant.device.1.status=disabled' > "$test_root/system.cfg"
printf 'old-network\n' > "$test_root/associated"
scan_count_before_gui=$(sed -n '1p' "$MOCK_IWLIST_COUNT_FILE")
"$manager" auto >/dev/null
grep -q 'external airOS configuration detected target=manual-target' "$test_root/wifi.log"
grep -q 'external airOS transition protected target=manual-target' "$test_root/wifi.log"
[ "$(sed -n '1p' "$MOCK_IWLIST_COUNT_FILE")" -eq "$scan_count_before_gui" ]
[ "$(sed -n '1p' "$test_root/associated")" = old-network ]
[ ! -e "$test_root/profiles/manual-target" ]

# The GUI configuration becomes a persistent profile only after the target is
# associated and the mocked DHCP, default-route, and Internet checks pass.
printf 'manual-target\n' > "$test_root/associated"
"$manager" auto >/dev/null
[ -f "$test_root/profiles/manual-target" ]
grep -q '^wireless.1.ssid=manual-target$' "$test_root/profiles/manual-target"
grep -Fqx 'wireless.1.scan_list.status=enabled' "$test_root/profiles/manual-target"
grep -Fqx "wireless.1.scan_list.channels=$standard_scan_frequencies" \
    "$test_root/profiles/manual-target"
grep -q 'applying standard scan-frequency allowlist reason=external-gui' "$test_root/wifi.log"
grep -q 'saved profile source=gui profile=manual-target' "$test_root/wifi.log"
grep -q 'external airOS connection saved target=manual-target' "$test_root/wifi.log"
[ ! -e "$test_root/state/gui-transition-started" ]
[ ! -e "$test_root/state/gui-transition-target" ]

# Expiry does not accidentally start the older 120-second generic transition
# window. The prior healthy association is eligible immediately after expiry.
printf '%s\n' \
    'wireless.1.ssid=expired-target' \
    'wpasupplicant.status=disabled' \
    'wpasupplicant.device.1.status=disabled' > "$test_root/system.cfg"
printf 'old-network\n' > "$test_root/associated"
export UBNT_GUI_GRACE_SECONDS=0
"$manager" auto >/dev/null
unset UBNT_GUI_GRACE_SECONDS
grep -q 'external airOS transition grace expired target=expired-target' "$test_root/wifi.log"
[ ! -e "$test_root/state/transition_started" ]
[ ! -e "$test_root/state/gui-transition-started" ]

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
[ ! -e "$test_root/state/paused" ]
[ ! -e "$test_root/state/manual-hold" ]
[ "$(sed -n '1p' "$test_root/associated")" = 'A Network With Spaces' ]

rm -f "$test_root/state/paused"
printf '%s\n' \
    'dendelion' \
    'wpa' \
    'D8:EC:5E:8D:6A:3A' \
    'new-test-password' | "$manager" provision-stdin >/dev/null
[ ! -e "$test_root/state/paused" ]
[ ! -e "$test_root/state/manual-hold" ]
[ -f "$test_root/profiles/dendelion" ]
grep -q '^wpasupplicant.profile.1.network.1.ssid=dendelion$' "$test_root/profiles/dendelion"
grep -q '^wpasupplicant.profile.1.network.1.bssid=D8:EC:5E:8D:6A:3A$' "$test_root/profiles/dendelion"
grep -q '^wpasupplicant.profile.1.network.1.psk=new-test-password$' "$test_root/profiles/dendelion"
grep -Fqx 'wireless.1.scan_list.status=enabled' "$test_root/profiles/dendelion"
grep -Fqx "wireless.1.scan_list.channels=$standard_scan_frequencies" \
    "$test_root/profiles/dendelion"
! grep -q 'new-test-password' "$test_root/wifi.log"
! find "$test_root/profiles" -maxdepth 1 -name '.dashboard-new.*' | grep -q .

printf '%s\n' \
    'WPA Template' \
    'change' \
    'rotated-test-password' \
    '02:11:22:33:44:55' \
    '17' \
    'ewma_ht' \
    'disabled' \
    '4' \
    'no' | "$manager" update-profile-stdin >/dev/null
grep -q '^aaa.1.wpa.psk=rotated-test-password$' "$test_root/profiles/WPA Template"
grep -q '^wpasupplicant.profile.1.network.1.psk=rotated-test-password$' \
    "$test_root/profiles/WPA Template"
grep -q '^wireless.1.ap=02:11:22:33:44:55$' "$test_root/profiles/WPA Template"
grep -q '^wpasupplicant.profile.1.network.1.bssid=02:11:22:33:44:55$' \
    "$test_root/profiles/WPA Template"
grep -q '^radio.1.txpower=17$' "$test_root/profiles/WPA Template"
grep -q '^radio.rate_module=ewma_ht$' "$test_root/profiles/WPA Template"
grep -q '^radio.1.rate.auto=disabled$' "$test_root/profiles/WPA Template"
grep -q '^radio.1.rate.mcs=4$' "$test_root/profiles/WPA Template"
[ "$(grep -c '^aaa.1.wpa.psk=' "$test_root/profiles/WPA Template")" -eq 1 ]
[ "$(grep -c '^wpasupplicant.profile.1.network.1.psk=' "$test_root/profiles/WPA Template")" -eq 1 ]
password_backup=$(find "$test_root/profiles/.disabled" -type f \
    -name 'WPA Template.settings-backup.*' | sed -n '1p')
[ -n "$password_backup" ]
grep -q '^aaa.1.wpa.psk=template-secret$' "$password_backup"
! grep -q 'rotated-test-password' "$test_root/wifi.log"
! find "$test_root/profiles" -maxdepth 1 -name '.dashboard-password.*' | grep -q .

if printf '%s\n' \
    'A Network With Spaces' 'change' 'password-update' '' '20' \
    'atheros' 'enabled' '15' 'no' | \
    "$manager" update-profile-stdin >/dev/null; then
    echo 'Open-network password update unexpectedly succeeded.' >&2
    exit 1
fi

# Forget a disconnected profile without disturbing the current radio/hold.
export MOCK_CFGMTD_HANGUP=1
printf 'WPA Template\n' | "$manager" forget-stdin >/dev/null
unset MOCK_CFGMTD_HANGUP
[ ! -e "$test_root/profiles/WPA Template" ]
find "$test_root/profiles/.disabled" -name 'WPA Template.forgotten.*' | grep -q .
grep -qx 'pi-rsa-key' "$test_root/authorized_keys"

# Active forget clears the manual hold and targets a different saved network.
printf 'dendelion\n' | "$manager" forget-stdin >/dev/null
[ ! -e "$test_root/profiles/dendelion" ]
[ ! -e "$test_root/state/paused" ]
[ "$(sed -n '1p' "$test_root/associated")" != dendelion ]
grep -qx 'pi-rsa-key' "$test_root/authorized_keys"
if printf 'reset\n' | "$manager" forget-stdin >/dev/null; then
    echo 'Internal reset profile should not be removable' >&2
    exit 1
fi

# Dashboard/CLI selections get bounded onboarding protection, never an
# implicit maintenance pause. Use current kernel uptime so this runs in BusyBox.
cp "$profile" "$test_root/system.cfg"
printf 'A Network With Spaces\n' > "$test_root/associated"
rm -f "$test_root/state/observed-system-config.md5" "$test_root/state/transition_started"
export MOCK_PING_STATUS=1
if printf 'A Network With Spaces\n' | "$manager" manual-connect-stdin >/dev/null; then
    echo 'Captive portal unexpectedly reported Internet ready.' >&2
    exit 1
else
    [ "$?" -eq 2 ]
fi
[ ! -e "$test_root/state/paused" ]
[ -f "$test_root/state/manual-hold" ]
hold_start=$(sed -n '1p' "$test_root/state/manual-hold")
"$manager" dashboard-status | awk -F'|' '$1 == "state" {exit !(NF == 9 && $5 == "yes" && $9 > 0 && $9 <= 600)}'
: > "$MOCK_IWLIST_COUNT_FILE"
"$manager" auto >/dev/null
"$manager" auto >/dev/null
[ ! -s "$MOCK_IWLIST_COUNT_FILE" ]
[ "$(sed -n '1p' "$test_root/state/manual-hold")" = "$hold_start" ]
grep -q 'captive portal protection' "$test_root/wifi.log"

# A lost radio link ends the portal hold at the two-minute connection deadline.
now=$(awk '{split($1,a,"."); print a[1]}' "$UBNT_UPTIME_FILE")
printf '%s\n%s\n' "$((now - 121))" 'A Network With Spaces' > "$test_root/state/manual-hold"
export MOCK_CCQ=0
"$manager" auto >/dev/null
unset MOCK_CCQ
[ ! -e "$test_root/state/manual-hold" ]
[ -s "$MOCK_IWLIST_COUNT_FILE" ]
grep -q 'manual target unavailable; automatic selection resumed' "$test_root/wifi.log"

# Ten-minute portal expiry cannot be extended by polls or another grace window.
now=$(awk '{split($1,a,"."); print a[1]}' "$UBNT_UPTIME_FILE")
printf '%s\n%s\n' "$((now - 601))" 'A Network With Spaces' > "$test_root/state/manual-hold"
export UBNT_FAILURES_BEFORE_SWITCH=1
: > "$MOCK_IWLIST_COUNT_FILE"
"$manager" auto >/dev/null
unset UBNT_FAILURES_BEFORE_SWITCH
[ ! -e "$test_root/state/manual-hold" ]
[ -s "$MOCK_IWLIST_COUNT_FILE" ]
grep -q 'manual connection protection expired; automatic selection resumed' "$test_root/wifi.log"

# Login success releases protection immediately, preserving the healthy link.
unset MOCK_PING_STATUS
printf '%s\n%s\n' "$now" 'A Network With Spaces' > "$test_root/state/manual-hold"
"$manager" auto >/dev/null
[ ! -e "$test_root/state/manual-hold" ]
[ "$(cat "$test_root/associated")" = 'A Network With Spaces' ]

# Maintenance pause remains explicit and indefinite; resume clears both kinds.
"$manager" pause >/dev/null
printf '%s\n%s\n' "$((now - 601))" 'A Network With Spaces' > "$test_root/state/manual-hold"
: > "$MOCK_IWLIST_COUNT_FILE"
"$manager" auto >/dev/null
[ -f "$test_root/state/paused" ]
[ ! -s "$MOCK_IWLIST_COUNT_FILE" ]
"$manager" resume >/dev/null
[ ! -e "$test_root/state/paused" ]
[ ! -e "$test_root/state/manual-hold" ]

# A competing manual request cannot set a hold when it cannot acquire the lock.
mkdir "$test_root/state/lock"
printf '%s\n' "$$" > "$test_root/state/lock/pid"
if printf 'A Network With Spaces\n' | "$manager" manual-connect-stdin >/dev/null; then
    echo 'Locked manual operation unexpectedly succeeded.' >&2
    exit 1
fi
[ ! -e "$test_root/state/manual-hold" ]
[ ! -e "$test_root/state/paused" ]
rm "$test_root/state/lock/pid"
rmdir "$test_root/state/lock"

# Fresh observations select the matching AP and security, not a stronger
# unrelated BSSID or an identically named open network. Saved profiles stay
# unchanged, and the next full survey clears the runtime-only channel pin.
printf '%s\n' 'wireless.1.ap=00:11:22:33:44:55' >> "$profile"
profile_hash_before=$(md5 -q "$profile" 2>/dev/null || md5sum "$profile" | awk '{print $1}')
printf '%s\n' \
    '90|A Network With Spaces|none|2462|11|00:11:22:33:44:66|-30' \
    '95|A Network With Spaces|wpa|2412|1|00:11:22:33:44:55|-20' \
    '55|A Network With Spaces|none|2437|6|00:11:22:33:44:55|-44' > "$test_root/state/scan.results"
now=$(awk '{split($1,a,"."); print a[1]}' "$UBNT_UPTIME_FILE")
printf '%s\n' "$now" > "$test_root/state/scan-completed"
export MOCK_RELOAD_CHANNELS="$test_root/reload-channels"
printf 'old-network\n' > "$test_root/associated"
"$manager" connect 'A Network With Spaces' >/dev/null
[ "$(cat "$MOCK_RELOAD_CHANNELS")" = 2437 ]
profile_hash_after=$(md5 -q "$profile" 2>/dev/null || md5sum "$profile" | awk '{print $1}')
[ "$profile_hash_before" = "$profile_hash_after" ]

# Saving never hides the radio's actual pin from the next full scan.
"$manager" save-current 'Fast saved copy' >/dev/null
grep -Fqx "wireless.1.scan_list.channels=$standard_scan_frequencies" "$test_root/profiles/Fast saved copy"
grep -Fqx 'wireless.1.scan_list.channels=2437' "$test_root/system.cfg"
cp "$test_root/state/scan.results" "$test_root/hint-results"
"$manager" dashboard-scan >/dev/null
grep -Fqx "wireless.1.scan_list.channels=$standard_scan_frequencies" "$test_root/system.cfg"
grep -q '|2412|1|' "$test_root/state/scan.results"
grep -q '|2462|11|' "$test_root/state/scan.results"

# A failed targeted attempt falls back to all channels in the same request.
cp "$test_root/hint-results" "$test_root/state/scan.results"
printf '%s\n' "$now" > "$test_root/state/scan-completed"
printf 'old-network\n' > "$test_root/associated"
: > "$MOCK_RELOAD_CHANNELS"
export MOCK_FAIL_FREQUENCY=2437 UBNT_ASSOCIATE_FAST_SECONDS=0
"$manager" connect 'A Network With Spaces' >/dev/null
unset MOCK_FAIL_FREQUENCY UBNT_ASSOCIATE_FAST_SECONDS
[ "$(sed -n '1p' "$MOCK_RELOAD_CHANNELS")" = 2437 ]
[ "$(sed -n '2p' "$MOCK_RELOAD_CHANNELS")" = "$standard_scan_frequencies" ]
[ "$(wc -l < "$MOCK_RELOAD_CHANNELS" | tr -d '[:space:]')" -eq 2 ]
[ "$(cat "$test_root/associated")" = 'A Network With Spaces' ]

# Stale, unknown-age and nonstandard-channel observations cannot pin a radio.
for hint_case in stale missing shifted; do
    printf '%s\n' "$now" > "$test_root/state/scan-completed"
    cp "$test_root/hint-results" "$test_root/state/scan.results"
    case $hint_case in
        stale) printf '%s\n' "$((now - 301))" > "$test_root/state/scan-completed" ;;
        missing) rm "$test_root/state/scan-completed" ;;
        shifted) sed 's/2437/2439/' "$test_root/hint-results" > "$test_root/state/scan.results" ;;
    esac
    printf 'old-network\n' > "$test_root/associated"
    : > "$MOCK_RELOAD_CHANNELS"
    "$manager" connect 'A Network With Spaces' >/dev/null
    [ "$(cat "$MOCK_RELOAD_CHANNELS")" = "$standard_scan_frequencies" ]
done

printf 'wifi-manager: ok\n'
