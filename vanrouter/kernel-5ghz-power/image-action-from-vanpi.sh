#!/bin/bash
# Preflight or flash the patched/official image from vanpi. This script refuses
# Wi-Fi routing to the router and reruns the existing verified backup exporter
# before staging either image.
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
# shellcheck source=release.conf
. "$script_dir/release.conf"

usage() {
	cat <<EOF
Usage:
  $0 preflight patched|rollback KIT_DIR [root@192.168.6.1]
  $0 execute patched|rollback KIT_DIR IMAGE_SHA256 [root@192.168.6.1]

Run this on vanpi. Preflight is non-flashing but creates a fresh verified
router backup and stages/tests the selected image in the router's /tmp.
Execute repeats every check and requires the full image SHA-256 as confirmation.
EOF
}

fail() {
	printf 'image-action-from-vanpi: %s\n' "$1" >&2
	exit 1
}

kit_value() {
	local key=$1 value
	value=$(sed -n "s/^$key=//p" "$kit_dir/KIT-INFO.txt")
	[ -n "$value" ] || fail "KIT-INFO.txt is missing $key"
	[ "$(printf '%s\n' "$value" | wc -l)" -eq 1 ] \
		|| fail "KIT-INFO.txt repeats $key"
	printf '%s\n' "$value"
}

[ "$#" -ge 3 ] || { usage >&2; exit 2; }
operation=$1
role=$2
kit_dir=$3
shift 3
case $operation in
	preflight)
		[ "$#" -le 1 ] || { usage >&2; exit 2; }
		confirmation=
		target=${1:-root@192.168.6.1}
		;;
	execute)
		[ "$#" -ge 1 ] && [ "$#" -le 2 ] || { usage >&2; exit 2; }
		confirmation=$1
		target=${2:-root@192.168.6.1}
		;;
	*) usage >&2; exit 2 ;;
esac
case $role in
	patched|rollback) ;;
	*) usage >&2; exit 2 ;;
esac
case $target in
	root@?*) ;;
	*) fail "unsafe SSH target: $target" ;;
esac
case $target in
	*[!A-Za-z0-9_.@-]*) fail "unsafe SSH target: $target" ;;
esac
[ -d "$kit_dir" ] || fail "kit directory does not exist: $kit_dir"
kit_dir=$(CDPATH= cd -- "$kit_dir" && pwd)

for required_command in /usr/bin/ssh /usr/bin/scp /usr/bin/sha256sum \
	/usr/bin/sed /usr/bin/awk /usr/bin/jq /usr/bin/id /usr/sbin/ip \
	/bin/cat; do
	[ -x "$required_command" ] || fail "required command is missing: $required_command"
done
[ -r "$kit_dir/SHA256SUMS" ] || fail "kit has no SHA256SUMS"
(
	cd "$kit_dir"
	/usr/bin/sha256sum -c SHA256SUMS >/dev/null
) || fail "recovery-kit checksum verification failed"

kit_release=$(kit_value OPENWRT_RELEASE)
kit_commit=$(kit_value OPENWRT_SOURCE_COMMIT)
kit_board=$(kit_value OPENWRT_BOARD)
kit_compat=$(kit_value OPENWRT_COMPAT_VERSION)
[ "$kit_release" = "$OPENWRT_RELEASE" ] || fail "kit release mismatch"
[ "$kit_commit" = "$OPENWRT_SOURCE_COMMIT" ] || fail "kit source mismatch"
[ "$kit_board" = "$OPENWRT_BOARD" ] || fail "kit board mismatch"
[ "$kit_compat" = "$OPENWRT_COMPAT_VERSION" ] \
	|| fail "kit compatibility-version mismatch"

case $role in
	patched)
		image_name=$(kit_value PATCHED_IMAGE)
		expected_hash=$(kit_value PATCHED_IMAGE_SHA256)
		;;
	rollback)
		image_name=$(kit_value OFFICIAL_SYSUPGRADE)
		expected_hash=$(kit_value OFFICIAL_SYSUPGRADE_SHA256)
		;;
esac
case $image_name in
	*[!A-Za-z0-9_.+-]*|'') fail "unsafe image filename in kit" ;;
esac
[ -r "$kit_dir/$image_name" ] || fail "selected image is missing: $image_name"
actual_hash=$(/usr/bin/sha256sum "$kit_dir/$image_name" | /usr/bin/awk '{print $1}')
[ "$actual_hash" = "$expected_hash" ] || fail "selected image checksum mismatch"
if [ "$operation" = execute ]; then
	[ "$confirmation" = "$expected_hash" ] \
		|| fail "confirmation must equal the full selected-image SHA-256"
fi

host=${target#root@}
route=$(/usr/sbin/ip -4 route get "$host") \
	|| fail "cannot resolve a route to $host"
case " $route " in
	*' dev eth0 '*) ;;
	*) fail "router path is not wired eth0: $route" ;;
esac
[ -r /sys/class/net/eth0/carrier ] || fail "eth0 carrier state is unavailable"
[ "$(/bin/cat /sys/class/net/eth0/carrier)" = 1 ] || fail "eth0 has no carrier"

backup_command=/home/pi/scripts/backup/openwrt_backup.sh
[ -x "$backup_command" ] || fail "verified backup command is missing: $backup_command"
if [ "$(/usr/bin/id -u)" -eq 0 ]; then
	"$backup_command"
else
	/usr/bin/sudo -n "$backup_command" \
		|| fail "fresh verified backup failed (passwordless sudo is required)"
fi

ssh_options=(
	-o BatchMode=yes
	-o ConnectTimeout=10
	-o ServerAliveInterval=5
	-o ServerAliveCountMax=3
)
remote_image=/tmp/dendelion-image-action-$$.itb
remote_staged=0
cleanup_remote() {
	if [ "$operation" = preflight ] && [ "$remote_staged" -eq 1 ]; then
		/usr/bin/ssh "${ssh_options[@]}" "$target" \
			"/bin/rm -f '$remote_image'" >/dev/null 2>&1 || true
	fi
}
trap cleanup_remote EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
board_json=$(/usr/bin/ssh "${ssh_options[@]}" "$target" \
	'/bin/ubus call system board') || fail "could not read router board metadata"
printf '%s\n' "$board_json" | /usr/bin/jq -e \
	--arg board "$OPENWRT_BOARD" --arg release "$OPENWRT_RELEASE" \
	--arg revision "$OPENWRT_REVISION" \
	--arg target "$OPENWRT_TARGET/$OPENWRT_SUBTARGET" \
	'(.board_name == $board) and (.release.version == $release) and
	 (.release.revision == $revision) and (.release.target == $target)' >/dev/null \
	|| fail "live router board, release, revision, or target does not match"
compat_json=$(/usr/bin/ssh "${ssh_options[@]}" "$target" \
	'/bin/cat /etc/board.json') || fail "could not read router compatibility metadata"
printf '%s\n' "$compat_json" | /usr/bin/jq -e \
	--arg compat "$OPENWRT_COMPAT_VERSION" \
	'.system.compat_version == $compat' >/dev/null \
	|| fail "live router compatibility version does not match the image"

/usr/bin/ssh "${ssh_options[@]}" "$target" \
	'test -x /sbin/sysupgrade && test -x /sbin/start-stop-daemon' \
	|| fail "router is missing required upgrade commands"
remote_staged=1
/usr/bin/scp -q -O "${ssh_options[@]}" "$kit_dir/$image_name" \
	"$target:$remote_image"
remote_hash=$(/usr/bin/ssh "${ssh_options[@]}" "$target" \
	"/usr/bin/sha256sum '$remote_image' | /usr/bin/awk '{print \$1}'") \
	|| fail "could not checksum the staged router image"
[ "$remote_hash" = "$expected_hash" ] || fail "staged router image checksum mismatch"
/usr/bin/ssh "${ssh_options[@]}" "$target" \
	"/sbin/sysupgrade -T '$remote_image'" >/dev/null \
	|| fail "router rejected the staged image"

if [ "$operation" = preflight ]; then
	/usr/bin/ssh "${ssh_options[@]}" "$target" \
		"/bin/rm -f '$remote_image'" >/dev/null
	remote_staged=0
	printf 'Preflight passed for %s image %s.\n' "$role" "$image_name"
	printf 'Full confirmation SHA-256: %s\n' "$expected_hash"
	printf 'No firmware was flashed.\n'
	exit 0
fi

/usr/bin/ssh "${ssh_options[@]}" "$target" \
	"/usr/bin/logger -p auth.notice -t dendelion-image-action 'starting $role image'; /sbin/start-stop-daemon -S -b -x /sbin/sysupgrade -- -v '$remote_image'"
printf 'The %s sysupgrade was started. The router should disconnect and reboot.\n' \
	"$role"
