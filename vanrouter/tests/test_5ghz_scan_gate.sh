#!/bin/bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
project_dir="$script_dir/../5ghz-scan-gate"
readme="$project_dir/README.md"
deploy="$project_dir/deploy-service.sh"
remote_action="$project_dir/service-action.sh"
service_config="$project_dir/files/etc/config/apsta-scan-gate"
service_init="$project_dir/files/etc/init.d/apsta-scan-gate"
daemon="$project_dir/files/usr/libexec/apsta-scan-gate.uc"
module_dir="$project_dir/files/usr/share/ucode"
policy="$module_dir/apsta_scan_gate/policy.uc"
policy_runner="$project_dir/tests/run-service-policy.sh"
daemon_runner="$project_dir/tests/run-service-daemon-integration.sh"
init_harness="$project_dir/tests/test-service-init.sh"

for script in "$deploy" "$remote_action" "$service_init" \
	"$policy_runner" "$daemon_runner" "$init_harness"; do
	[ -f "$script" ] || { printf 'missing script: %s\n' "$script" >&2; exit 1; }
done
/bin/bash -n "$deploy"
/bin/bash -n "$script_dir/test_5ghz_scan_gate.sh"
/bin/sh -n "$remote_action"
/bin/sh -n "$service_init"
/bin/sh -n "$policy_runner"
/bin/sh -n "$daemon_runner"
/bin/sh -n "$init_harness"

for executable in "$deploy" "$remote_action" "$service_init" \
	"$policy_runner" "$daemon_runner" "$init_harness"; do
	[ -x "$executable" ] || { printf 'script is not executable: %s\n' "$executable" >&2; exit 1; }
done

grep -q "^[[:space:]]*option enabled '0'[[:space:]]*$" "$service_config"
grep -q "^[[:space:]]*option radio 'radio1'[[:space:]]*$" "$service_config"
grep -q "^[[:space:]]*option station_section 'wifinet4'[[:space:]]*$" "$service_config"
grep -q "^[[:space:]]*option station_network 'clientwan'[[:space:]]*$" "$service_config"
grep -q "^[[:space:]]*option ap_section 'dendelion_5g'[[:space:]]*$" "$service_config"

for statement in \
	'standalone service is implemented, default-disabled' \
	'UCI configuration alone cannot express this behavior' \
	'One PHY still cannot beacon' \
	'has not been installed, enabled, or deployed' \
	'31 deterministic cases' \
	'does not issue `wifi reload`' \
	'Bluetooth TTL module is not treated as a dependable boot console'; do
	grep -Fi "$statement" "$readme" >/dev/null || {
		printf 'README is missing required statement: %s\n' "$statement" >&2
		exit 1
	}
done
for command in \
	'--install-disabled root@192.168.6.1' \
	'APSTA_SCAN_GATE_RECOVERY=radio0' \
	'--activate root@192.168.6.1' \
	'--disable root@192.168.6.1' \
	'wifi down radio1' \
	'wifi up radio1'; do
	grep -F -- "$command" "$readme" >/dev/null || {
		printf 'README is missing lifecycle command: %s\n' "$command" >&2
		exit 1
	}
done
if grep -Ei 'uninterrupted|zero[- ]downtime|always available' "$readme" >/dev/null; then
	printf 'README overstates same-radio AP availability\n' >&2
	exit 1
fi

for mode in --check --stage-only --install-disabled --activate --status --disable --remove; do
	grep -F -- "$mode" "$deploy" >/dev/null || {
		printf 'deploy helper is missing mode: %s\n' "$mode" >&2
		exit 1
	}
done
grep -F 'APSTA_SCAN_GATE_RECOVERY' "$deploy" >/dev/null
grep -F 'BatchMode=yes' "$deploy" >/dev/null
grep -F 'mktemp -d /tmp/apsta-scan-gate.XXXXXX' "$deploy" >/dev/null
grep -F 'sha256sum -c manifest.sha256' "$remote_action" >/dev/null
grep -F "option enabled '0'" "$remote_action" >/dev/null
grep -F "'../init.d/apsta-scan-gate'" "$remote_action" >/dev/null
grep -F 'boot_links_present=%s canonical_boot_links=%s procd_registered=%s' \
	"$remote_action" >/dev/null
grep -F 'live_daemon_present' "$remote_action" >/dev/null
grep -F 'boot/config state is unverified' "$remote_action" >/dev/null
grep -F "1|true|yes|on|enabled" "$remote_action" >/dev/null

for required in \
	'libubus.connect()' \
	'network.wireless", "status"' \
	'wpa_supplicant", "bss_info"' \
	'hostapd", "apsta_state"' \
	'bus.subscriber' \
	'bus.listener("ubus.object.add"' \
	'bus.publish(SERVICE' \
	'uloop.signal("SIGTERM"'; do
	grep -F "$required" "$daemon" >/dev/null || {
		printf 'daemon is missing required API/lifecycle call: %s\n' "$required" >&2
		exit 1
	}
done
grep -F 'return channel in [ 36, 40, 44, 48, 149, 153, 157, 161, 165 ]' "$daemon" >/dev/null
grep -F 'sta_count != 1' "$daemon" >/dev/null
grep -F 'scan_list_present' "$daemon" >/dev/null
grep -F 'policy.release()' "$daemon" >/dev/null
grep -F 'connected-repair' "$policy" >/dev/null
grep -F '"DISCONNECT"' "$policy" >/dev/null
grep -F '"BSS_FLUSH 0"' "$policy" >/dev/null
grep -F '"RECONNECT"' "$policy" >/dev/null
grep -F 'event_is(event' "$policy" >/dev/null
grep -F 'association_started' "$policy" >/dev/null

if grep -Ei 'lion_fone|bssid|sae_password|wireless key' \
	"$daemon" "$policy" "$deploy" "$remote_action" "$service_init" >/dev/null; then
	printf 'generic service code contains a site SSID or credential-bearing field\n' >&2
	exit 1
fi
if grep -F 'wifi down' "$daemon" "$policy" "$deploy" "$remote_action" "$service_init" >/dev/null ||
	grep -F 'wifi up' "$daemon" "$policy" "$deploy" "$remote_action" "$service_init" >/dev/null; then
	printf 'service code must not reload a radio implicitly\n' >&2
	exit 1
fi

"$init_harness" "$service_init"

if [ -n "${UCODE:-}" ]; then
	[ -x "$UCODE" ] || { printf 'UCODE is not executable: %s\n' "$UCODE" >&2; exit 2; }
	"$UCODE" -L "$module_dir" \
		-cno-interp,dynlink=ubus,dynlink=uloop,dynlink=uci \
		-o /dev/null "$daemon"
	UCODE="$UCODE" "$policy_runner"
	UCODE="$UCODE" "$daemon_runner"
fi

printf '5 GHz AP/STA standalone-service tests: passed\n'
