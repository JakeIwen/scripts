#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
daemon="$project_dir/files/usr/libexec/apsta-scan-gate.uc"
module_dir="$project_dir/files/usr/share/ucode"
mock_dir="$script_dir/mocks"
temp_base=${TMPDIR:-/tmp}
temp_base=${temp_base%/}
output=$(mktemp "$temp_base/apsta-scan-gate-daemon-test.XXXXXX")
case $output in
	"$temp_base"/apsta-scan-gate-daemon-test.*) ;;
	*) printf 'daemon integration: unsafe temporary path\n' >&2; exit 1 ;;
esac
cleanup() {
	rm -f -- "$output"
}
trap cleanup EXIT HUP INT TERM

run_ucode() {
	if [ -n "${UCODE_ROOTFS:-}" ]; then
		rootfs=${UCODE_ROOTFS%/}
		[ -x "$rootfs/lib/libc.so" ] || {
			printf 'daemon integration: target libc is unavailable\n' >&2
			exit 2
		}
		"$rootfs/lib/libc.so" --library-path "$rootfs/lib:$rootfs/usr/lib" \
			"$rootfs/usr/bin/ucode" "$@"
		return
	fi

	ucode_bin=${UCODE:-}
	if [ -z "$ucode_bin" ]; then
		ucode_bin=$(command -v ucode 2>/dev/null || true)
	fi
	[ -n "$ucode_bin" ] && [ -x "$ucode_bin" ] || {
		printf 'daemon integration: set UCODE or UCODE_ROOTFS\n' >&2
		exit 2
	}
	"$ucode_bin" "$@"
}

run_ucode -L "$mock_dir" -L "$module_dir" \
	-D 'DAEMON_TEST={"scenario":"disabled"}' "$daemon" > "$output" 2>&1
grep -F 'TRACE uci get_all apsta-scan-gate main' "$output" >/dev/null
if grep -Eq '^TRACE (ubus|uloop) ' "$output"; then
	printf 'daemon integration: disabled service touched ubus or uloop\n' >&2
	exit 1
fi

run_ucode -L "$mock_dir" -L "$module_dir" \
	-D 'DAEMON_TEST={"scenario":"enabled","wpa_state":"DISCONNECTED","ap_up":true,"ap_frequency":5745,"reconnect_attempts":0}' \
	"$daemon" > "$output" 2>&1

grep -F 'TRACE ubus publish apsta_scan_gate' "$output" >/dev/null
grep -F 'TRACE uloop timer 1' "$output" >/dev/null
publish_line=$(grep -nF 'TRACE ubus publish apsta_scan_gate' "$output" | head -1 | cut -d: -f1)
reconcile_line=$(grep -nF 'TRACE uloop timer 1' "$output" | head -1 | cut -d: -f1)
[ "$publish_line" -lt "$reconcile_line" ] || {
	printf 'daemon integration: reconcile was queued before status API publication\n' >&2
	exit 1
}

startup=$(sed -n 's/^RESULT startup //p' "$output")
release=$(sed -n 's/^RESULT release //p' "$output")
[ -n "$startup" ] && [ -n "$release" ] || {
	printf 'daemon integration: mock event loop did not report status\n' >&2
	exit 1
}
printf '%s\n' "$startup" | jq -e '
  .enabled == true and .paused == false and .phase == "parked" and
  .fallback_frequency == 5745 and .wpa_state == "DISCONNECTED" and
  .ap_status == "ENABLED" and .ap_frequency == 5745 and
  .stock_resumed == null and .policy.timers.retry > 0
' >/dev/null
printf '%s\n' "$release" | jq -e '
  .enabled == true and .paused == true and .phase == "stopped" and
  .stock_resumed == false and
  .last_error == "stock reconnect was not acknowledged after three attempts" and
  (.policy.timers | length) == 0
' >/dev/null

[ "$(grep -F 'TRACE ubus call wpa_supplicant.wl1-sta0 control' "$output" | \
	grep -Fc '"RECONNECT"')" -eq 3 ] || {
	printf 'daemon integration: release did not make exactly three reconnect attempts\n' >&2
	exit 1
}
if grep -F 'must-never-be-logged' "$output" >/dev/null; then
	printf 'daemon integration: wireless secret escaped into output\n' >&2
	exit 1
fi

printf 'service daemon integration: passed\n'
