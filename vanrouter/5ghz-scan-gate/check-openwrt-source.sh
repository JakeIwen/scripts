#!/bin/bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
# shellcheck source=/dev/null
. "$script_dir/release.conf"

source_repo=${1:-"$script_dir/../../../openwrt"}
patch_file="$script_dir/$OPENWRT_PATCH"
policy_prelude="$script_dir/tests/policy-prelude.uc"
policy_cases="$script_dir/tests/policy-cases.uc"
temp_base=${TMPDIR:-/tmp}
temp_base=${temp_base%/}
test_root=$(mktemp -d "$temp_base/openwrt-scan-gate-check.XXXXXX")
case $test_root in
	"$temp_base"/openwrt-scan-gate-check.*) ;;
	*) printf 'unsafe temporary directory: %s\n' "$test_root" >&2; exit 1 ;;
esac
cleanup() {
	rm -rf -- "$test_root"
}
trap cleanup EXIT HUP INT TERM

fail() {
	printf 'scan-gate source check: %s\n' "$*" >&2
	exit 1
}

sha256_file() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | awk '{ print $1 }'
	else
		shasum -a 256 "$1" | awk '{ print $1 }'
	fi
}

[ -d "$source_repo/.git" ] || [ -f "$source_repo/.git" ] ||
	fail "not an OpenWrt Git checkout: $source_repo"
[ -r "$patch_file" ] || fail "patch is unreadable: $patch_file"
[ -r "$policy_prelude" ] || fail "policy prelude is unreadable: $policy_prelude"
[ -r "$policy_cases" ] || fail "policy cases are unreadable: $policy_cases"
[ "$OPENWRT_PATCH_SHA256" != PENDING ] || fail 'release.conf still has a pending patch hash'

resolved_commit=$(git -C "$source_repo" rev-parse "$OPENWRT_TAG^{commit}" 2>/dev/null) ||
	fail "source checkout does not contain $OPENWRT_TAG"
[ "$resolved_commit" = "$OPENWRT_COMMIT" ] ||
	fail "$OPENWRT_TAG resolves to $resolved_commit, expected $OPENWRT_COMMIT"

actual_hash=$(sha256_file "$patch_file")
[ "$actual_hash" = "$OPENWRT_PATCH_SHA256" ] ||
	fail "patch SHA-256 is $actual_hash, expected $OPENWRT_PATCH_SHA256"

expected_paths=$(printf '%s\n' \
	'package/network/config/wifi-scripts/Makefile' \
	'package/network/config/wifi-scripts/files-ucode/usr/share/schema/wireless.wifi-iface.json' \
	'package/network/config/wifi-scripts/files-ucode/usr/share/ucode/wifi/supplicant.uc' \
	'package/network/services/hostapd/Makefile' \
	'package/network/services/hostapd/files/wpa_supplicant.uc' |
	LC_ALL=C sort)
actual_paths=$(git apply --numstat "$patch_file" | awk '{ print $3 }' | LC_ALL=C sort)
[ "$actual_paths" = "$expected_paths" ] || {
	printf 'expected patched paths:\n%s\nactual patched paths:\n%s\n' \
		"$expected_paths" "$actual_paths" >&2
	exit 1
}

if grep -E 'lion_fone|dendelion|192\.168\.' "$patch_file" >/dev/null; then
	fail 'patch contains site-specific network identifiers'
fi

source_tree="$test_root/source"
mkdir -p "$source_tree"
git -C "$source_repo" archive "$OPENWRT_COMMIT" -- $expected_paths |
	tar -xf - -C "$source_tree"
git -C "$source_tree" init -q
git -C "$source_tree" config user.name 'OpenWrt scan-gate check'
git -C "$source_tree" config user.email 'scan-gate-check@example.invalid'
git -C "$source_tree" add .
git -C "$source_tree" commit -q -m baseline

(
	cd "$source_tree"
	patch --dry-run --silent --fuzz=0 -p1 < "$patch_file"
)
git -C "$source_tree" apply --check --whitespace=error-all "$patch_file"
git -C "$source_tree" apply --whitespace=error-all "$patch_file"
git -C "$source_tree" diff --check
git -C "$source_tree" apply -R --check --whitespace=error-all "$patch_file"
git -C "$source_tree" apply -R --whitespace=error-all "$patch_file"
git -C "$source_tree" diff --quiet || fail 'reverse application did not restore the pinned source'
git -C "$source_tree" apply --whitespace=error-all "$patch_file"

changed_paths=$(git -C "$source_tree" diff --name-only | LC_ALL=C sort)
[ "$changed_paths" = "$expected_paths" ] || fail 'applied diff changed an unexpected path'

schema="$source_tree/package/network/config/wifi-scripts/files-ucode/usr/share/schema/wireless.wifi-iface.json"
generator="$source_tree/package/network/config/wifi-scripts/files-ucode/usr/share/ucode/wifi/supplicant.uc"
controller="$source_tree/package/network/services/hostapd/files/wpa_supplicant.uc"

jq -e '
  .properties.apsta_scan_gate.type == "boolean" and
  .properties.apsta_scan_gate.default == false and
  .properties.apsta_retry_interval.type == "number" and
  .properties.apsta_retry_interval.default == 60 and
  .properties.apsta_retry_interval.minimum == 15 and
  .properties.apsta_retry_interval.maximum == 3600 and
  .properties.apsta_scan_timeout.type == "number" and
  .properties.apsta_scan_timeout.default == 15 and
  .properties.apsta_scan_timeout.minimum == 5 and
  .properties.apsta_scan_timeout.maximum == 60
' "$schema" >/dev/null || fail 'UCI schema options or bounds are wrong'

grep -Fx "PKG_RELEASE:=$WIFI_SCRIPTS_PKG_RELEASE" \
	"$source_tree/package/network/config/wifi-scripts/Makefile" >/dev/null ||
	fail 'wifi-scripts package release was not bumped'
grep -Fx "PKG_RELEASE:=$HOSTAPD_PKG_RELEASE" \
	"$source_tree/package/network/services/hostapd/Makefile" >/dev/null ||
	fail 'hostapd package release was not bumped'

for symbol in \
	apsta_scan_gate apsta_retry_interval apsta_scan_timeout \
	apsta_fallback_frequency; do
	grep -F "$symbol" "$generator" >/dev/null ||
		fail "wifi generator does not propagate $symbol"
	grep -F "$symbol" "$controller" >/dev/null ||
		fail "supplicant controller does not consume $symbol"
done
for invariant in \
	'const scan_gate_settle_ms = 500' \
	'const scan_gate_result_grace_ms = 1000' \
	'const scan_gate_disconnect_limit = 3' \
	'if (sta_ifaces != 1)' \
	'phase: scan_gate_state(iface) == "COMPLETED" ? "connected" : "idle"' \
	'for (let name in [ "watchdog", "settle", "retry", "grace" ])' \
	'if (!scan_gate_handle_state(ifname, iface, state))'; do
	grep -F "$invariant" "$controller" >/dev/null ||
		fail "missing state-machine guard: $invariant"
done
grep -F "interface.config.mode == 'sta' && interface.config.apsta_scan_gate" \
	"$generator" >/dev/null || fail 'scan gate is not opt-in and station-scoped'
grep -F '!interface.config.mlo && data.config.frequency' "$generator" >/dev/null ||
	fail 'scan gate does not require non-MLO and a fixed fallback frequency'

for invariant in \
	'gate.disconnect_ok = iface.ctrl("DISCONNECT") == "OK"' \
	'!gate.disconnect_ok || !scan_gate_is_parkable(state)' \
	'gate.disconnect_attempts < scan_gate_disconnect_limit' \
	'msg.frequency = gate.fallback_frequency' \
	'let error = ubus.error()' \
	'if (!scan_gate_hostapd_set(ifname, gate, false))' \
	'if (!scan_gate_hostapd_set(ifname, gate, true))' \
	'gate.phase = "fault"' \
	'gate.watchdog = uloop.timer(gate.timeout_ms' \
	'gate.retry = uloop.timer(gate.retry_ms' \
	'iface.ctrl("RECONNECT") != "OK"' \
	'match(ev, /^CTRL-EVENT-SCAN-RESULTS/)' \
	'match(ev, /^CTRL-EVENT-(NETWORK-NOT-FOUND|SCAN-FAILED)/)' \
	'if (!gate || gate.phase == "connected")'; do
	grep -F "$invariant" "$controller" >/dev/null ||
		fail "missing state-machine invariant: $invariant"
done

watchdog_line=$(grep -nF 'gate.watchdog = uloop.timer(gate.timeout_ms' "$controller" |
	head -1 | cut -d: -f1)
ap_stop_line=$(grep -nF 'if (!scan_gate_hostapd_set(ifname, gate, false))' "$controller" |
	head -1 | cut -d: -f1)
[ "$watchdog_line" -lt "$ap_stop_line" ] ||
	fail 'watchdog is not armed before the AP-stop call'

parking_line=$(grep -nF 'gate.phase = "parking"' "$controller" | head -1 | cut -d: -f1)
disconnect_line=$(grep -nF 'gate.disconnect_ok = iface.ctrl("DISCONNECT") == "OK"' \
	"$controller" | head -1 | cut -d: -f1)
[ "$parking_line" -lt "$disconnect_line" ] ||
	fail 'parking phase is not set before reentrant DISCONNECT'

ap_up_line=$(grep -nF 'if (!scan_gate_hostapd_set(ifname, gate, true))' "$controller" |
	head -1 | cut -d: -f1)
parked_line=$(grep -nF 'gate.phase = "parked"' "$controller" | head -1 | cut -d: -f1)
retry_timer_line=$(grep -nF 'gate.retry = uloop.timer(gate.retry_ms' "$controller" |
	head -1 | cut -d: -f1)
[ "$ap_up_line" -lt "$parked_line" ] && [ "$parked_line" -lt "$retry_timer_line" ] ||
	fail 'fallback AP acceptance is not verified before parking and retry'

retry_begin_line=$(awk '/AP\/STA scan gate retrying/{ retry = 1 } retry && /scan_gate_begin_window/{ print NR; exit }' \
	"$controller")
bss_flush_line=$(grep -nF 'iface.ctrl("BSS_FLUSH 0")' "$controller" | head -1 | cut -d: -f1)
reconnect_line=$(grep -nF 'iface.ctrl("RECONNECT") != "OK"' "$controller" |
	head -1 | cut -d: -f1)
[ "$retry_begin_line" -lt "$bss_flush_line" ] && [ "$bss_flush_line" -lt "$reconnect_line" ] ||
	fail 'retry does not stop the AP before BSS flush and RECONNECT'

event_line=$(grep -nF 'iface_event("state", ifname, event_data)' "$controller" |
	head -1 | cut -d: -f1)
gate_state_line=$(grep -nF 'scan_gate_handle_state(ifname, iface, state)' "$controller" |
	tail -1 | cut -d: -f1)
[ "$event_line" -lt "$gate_state_line" ] ||
	fail 'real supplicant state is not emitted before scan-gate hostapd handling'

grep -F 'scan_gate_remove(ifname);' "$controller" >/dev/null ||
	fail 'interface stop does not cancel scan-gate state'
grep -F 'for (let ifname in wpas.data.apsta_scan_gate)' "$controller" >/dev/null ||
	fail 'shutdown does not cancel every scan-gate timer'
grep -F 'scan_gate_remove(name);' "$controller" >/dev/null ||
	fail 'interface removal does not cancel scan-gate state'
grep -F 'AP/STA scan gate could not ${up ? "start" : "stop"} the AP' \
	"$controller" >/dev/null || fail 'hostapd transition failure lacks a diagnostic'
grep -F 'case "AUTHENTICATING":' "$controller" >/dev/null ||
	fail 'stock AUTHENTICATING AP-down behavior was removed'
grep -F 'case "COMPLETED":' "$controller" >/dev/null ||
	fail 'stock COMPLETED channel-following behavior was removed'

ucode_bin=${UCODE:-}
if [ -z "$ucode_bin" ] && command -v ucode >/dev/null 2>&1; then
	ucode_bin=$(command -v ucode)
fi
if [ -n "$ucode_bin" ]; then
	[ -x "$ucode_bin" ] || fail "UCODE is not executable: $ucode_bin"
	"$ucode_bin" -cno-interp,dynlink=uloop,dynlink=common,dynlink=fs \
		-o "$test_root/wpa_supplicant.ucb" "$controller"
	# A standalone ucode program may not export symbols. Strip only the unchanged
	# module export marker so the compiler can syntax-check this library directly.
	sed 's/^export function /function /' "$generator" > "$test_root/wifi-supplicant-parse.uc"
	"$ucode_bin" \
		-cno-interp,dynlink=wifi.common,dynlink=wifi.netifd,dynlink=wifi.iface,dynlink=fs \
		-o "$test_root/wifi-supplicant.ucb" "$test_root/wifi-supplicant-parse.uc"
	awk -v controller="$controller" '
		FILENAME == controller {
			if ($0 ~ /^const scan_gate_settle_ms/)
				copy = 1
			if ($0 ~ /^function iface_channel_switch/)
				copy = 0
			if (copy)
				print
			next
		}
		{ print }
	' "$policy_prelude" "$controller" "$policy_cases" > "$test_root/policy-test.uc"
	"$ucode_bin" "$test_root/policy-test.uc"
	printf 'ucode syntax: passed (%s)\n' "$ucode_bin"
else
	printf 'ucode syntax: skipped (set UCODE to a host ucode binary)\n'
fi

printf 'scan-gate source check: passed for %s at %s\n' \
	"$OPENWRT_TAG" "$OPENWRT_COMMIT"
