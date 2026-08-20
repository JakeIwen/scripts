#!/bin/sh
set -eu

init=${1:-}
[ -f "$init" ] || { printf 'init harness: missing init script\n' >&2; exit 2; }

temp_base=${TMPDIR:-/tmp}
temp_base=${temp_base%/}
transcript=$(mktemp "$temp_base/apsta-scan-gate-init.XXXXXX")
case $transcript in
	"$temp_base"/apsta-scan-gate-init.*) ;;
	*) printf 'init harness: unsafe temporary path\n' >&2; exit 1 ;;
esac
cleanup() {
	rm -f -- "$transcript"
}
trap cleanup EXIT HUP INT TERM

record() {
	printf '%s' "$1" >> "$transcript"
	shift
	for value in "$@"; do
		printf ' <%s>' "$value" >> "$transcript"
	done
	printf '\n' >> "$transcript"
}

config_load() { record config_load "$@"; }
config_get_bool() { record config_get_bool "$@"; eval "$1=1"; }
procd_open_instance() { record procd_open_instance "$@"; }
procd_set_param() { record procd_set_param "$@"; }
procd_close_instance() { record procd_close_instance "$@"; }
procd_add_reload_trigger() { record procd_add_reload_trigger "$@"; }
procd_send_signal() { record procd_send_signal "$@"; }
rc_procd() { record rc_procd "$@"; "$@"; }
ubus() { record ubus "$@"; return 0; }
sleep() { record sleep "$@"; }

# shellcheck source=/dev/null
. "$init"

[ "$START" -eq 99 ]
[ "$STOP" -eq 10 ]
[ "$USE_PROCD" -eq 1 ]
[ "$PROG" = /usr/bin/ucode ]
[ "$DAEMON" = /usr/libexec/apsta-scan-gate.uc ]

start_service
service_triggers
stop_service
reload_service

for expected in \
	'procd_set_param <command> </usr/bin/ucode> </usr/libexec/apsta-scan-gate.uc>' \
	'procd_set_param <user> <root>' \
	'procd_set_param <respawn> <3600> <5> <5>' \
	'procd_set_param <stdout> <1>' \
	'procd_set_param <stderr> <1>' \
	'procd_add_reload_trigger <apsta-scan-gate> <wireless>' \
	'ubus <call> <apsta_scan_gate> <shutdown> <{}>' \
	'procd_send_signal <apsta-scan-gate> <*> <HUP>'; do
	grep -F "$expected" "$transcript" >/dev/null || {
		printf 'init harness: missing transcript line: %s\n' "$expected" >&2
		exit 1
	}
done

[ "$(grep -c '^procd_open_instance' "$transcript")" -eq 2 ]
[ "$(grep -c '^procd_close_instance' "$transcript")" -eq 2 ]
printf 'service init harness: passed\n'
