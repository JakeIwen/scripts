#!/bin/bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
files_dir="$script_dir/files"
tests_dir="$script_dir/tests"
remote_helper="$script_dir/service-action.sh"
mode=${1:-}
target=${2:-root@192.168.6.1}

usage() {
	cat >&2 <<EOF
Usage: $0 --check|--stage-only|--install-disabled|--activate|--status|--disable|--remove [root@host]

Activation additionally requires APSTA_SCAN_GATE_RECOVERY=radio0 or ethernet.
EOF
	exit 2
}

case $mode in
	--check|--stage-only|--install-disabled|--activate|--status|--disable|--remove) ;;
	*) usage ;;
esac
case $target in
	''|-*|*[!A-Za-z0-9_.@+-]*) printf 'Refusing unsafe SSH target: %s\n' "$target" >&2; exit 2 ;;
esac

ssh_options=(-o BatchMode=yes -o ConnectTimeout=8)

local_check() {
	/bin/bash -n "$script_dir/deploy-service.sh"
	/bin/sh -n "$remote_helper"
	/bin/sh -n "$files_dir/etc/init.d/apsta-scan-gate"
	grep -q "^[[:space:]]*option enabled '0'[[:space:]]*$" \
		"$files_dir/etc/config/apsta-scan-gate"
	[ -x "$files_dir/etc/init.d/apsta-scan-gate" ] || {
		printf 'Init script is not executable in the toolkit\n' >&2
		exit 1
	}

	if [ -n "${UCODE:-}" ]; then
		[ -x "$UCODE" ] || { printf 'UCODE is not executable: %s\n' "$UCODE" >&2; exit 2; }
		"$UCODE" -L "$files_dir/usr/share/ucode" \
			-cno-interp,dynlink=ubus,dynlink=uloop,dynlink=uci \
			-o /dev/null "$files_dir/usr/libexec/apsta-scan-gate.uc"
		UCODE="$UCODE" "$tests_dir/run-service-policy.sh"
		UCODE="$UCODE" "$tests_dir/run-service-daemon-integration.sh"
	fi
}

run_streamed_action() {
	local action=$1 recovery=${2:-}
	ssh "${ssh_options[@]}" "$target" \
		"/bin/sh -s -- '$action' '' '$recovery'" < "$remote_helper"
}

local_check

case $mode in
	--check)
		run_streamed_action preflight
		exit 0
		;;
	--status)
		run_streamed_action status
		exit 0
		;;
	--disable)
		run_streamed_action disable
		exit 0
		;;
	--remove)
		run_streamed_action remove
		exit 0
		;;
	--activate)
		recovery=${APSTA_SCAN_GATE_RECOVERY:-}
		case $recovery in
			radio0|ethernet) ;;
			*) printf 'Set APSTA_SCAN_GATE_RECOVERY=radio0 or ethernet before activation\n' >&2; exit 2 ;;
		esac
		run_streamed_action activate "$recovery"
		exit 0
		;;
esac

temp_base=${TMPDIR:-/tmp}
temp_base=${temp_base%/}
valid_local_stage() {
	local value=$1 suffix
	[[ $value == "$temp_base"/apsta-scan-gate-local.* ]] || return 1
	suffix=${value#"$temp_base"/apsta-scan-gate-local.}
	[[ $suffix =~ ^[A-Za-z0-9]+$ ]]
}
valid_remote_stage() {
	[[ $1 =~ ^/tmp/apsta-scan-gate\.[A-Za-z0-9]+$ ]]
}
local_stage=$(mktemp -d "$temp_base/apsta-scan-gate-local.XXXXXX")
valid_local_stage "$local_stage" || {
	printf 'Refusing unsafe local staging path: %s\n' "$local_stage" >&2
	exit 1
}
remote_stage=
cleanup() {
	if valid_local_stage "$local_stage"; then
		rm -rf -- "$local_stage"
	fi
	if valid_remote_stage "$remote_stage"; then
		ssh "${ssh_options[@]}" "$target" "/bin/rm -rf -- '$remote_stage'" \
			>/dev/null 2>&1 || true
	fi
}
abort_cleanup() {
	trap - EXIT HUP INT TERM
	cleanup
	exit 130
}
trap cleanup EXIT
trap abort_cleanup HUP INT TERM

remote_stage=$(ssh "${ssh_options[@]}" "$target" \
	"mktemp -d /tmp/apsta-scan-gate.XXXXXX")
valid_remote_stage "$remote_stage" || {
	printf 'Refusing unsafe remote staging path: %s\n' "$remote_stage" >&2
	exit 1
}

mkdir -p "$local_stage/ucode/apsta_scan_gate"
cp "$files_dir/etc/config/apsta-scan-gate" "$local_stage/config"
cp "$files_dir/etc/init.d/apsta-scan-gate" "$local_stage/init"
cp "$files_dir/usr/libexec/apsta-scan-gate.uc" "$local_stage/daemon.uc"
cp "$files_dir/usr/share/ucode/apsta_scan_gate/policy.uc" \
	"$local_stage/ucode/apsta_scan_gate/policy.uc"
cp "$tests_dir/service-policy-cases.uc" "$local_stage/service-policy-cases.uc"
cp "$remote_helper" "$local_stage/service-action.sh"
(
	cd "$local_stage"
	shasum -a 256 config init daemon.uc ucode/apsta_scan_gate/policy.uc \
		service-policy-cases.uc service-action.sh > manifest.sha256
)

ssh "${ssh_options[@]}" "$target" \
	"mkdir -m 0700 '$remote_stage/ucode' '$remote_stage/ucode/apsta_scan_gate'"
scp -q -O "${ssh_options[@]}" \
	"$local_stage/config" "$local_stage/init" "$local_stage/daemon.uc" \
	"$local_stage/service-policy-cases.uc" "$local_stage/service-action.sh" \
	"$local_stage/manifest.sha256" "$target:$remote_stage/"
scp -q -O "${ssh_options[@]}" \
	"$local_stage/ucode/apsta_scan_gate/policy.uc" \
	"$target:$remote_stage/ucode/apsta_scan_gate/policy.uc"

ssh "${ssh_options[@]}" "$target" \
	"/bin/sh '$remote_stage/service-action.sh' validate-stage '$remote_stage'"

if [ "$mode" = --install-disabled ]; then
	ssh "${ssh_options[@]}" "$target" \
		"/bin/sh '$remote_stage/service-action.sh' install-disabled '$remote_stage'"
else
	printf 'stage-only: hashes, shell syntax, target ucode, and 31 policy cases passed; temporary stage cleanup was attempted\n'
fi
