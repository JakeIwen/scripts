#!/bin/bash
# Convert the verified installed-package manifest in a dendelion backup into
# the one-package-per-line seed consumed by build-openwrt.sh.
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
# shellcheck source=release.conf
. "$script_dir/release.conf"

usage() {
	printf 'Usage: %s VERIFIED_BACKUP NEW_PACKAGE_LIST\n' "$0"
}

fail() {
	printf 'packages-from-backup: %s\n' "$1" >&2
	exit 1
}

[ "$#" -eq 2 ] || { usage >&2; exit 2; }
backup_bundle=$1
output_file=$2
[ -r "$backup_bundle" ] || fail "backup bundle is unreadable: $backup_bundle"
[ ! -e "$output_file" ] || fail "output already exists: $output_file"
command -v jq >/dev/null 2>&1 || fail "required command is missing: jq"

umask 077
work=$(mktemp -d "${TMPDIR:-/tmp}/dendelion-packages.XXXXXX")
case $work in
	"${TMPDIR:-/tmp}"/dendelion-packages.*) ;;
	*) fail "refusing unexpected temporary path: $work" ;;
esac
cleanup() {
	rm -rf "$work"
}
trap cleanup EXIT HUP INT TERM

actual_members=$(tar -tzf "$backup_bundle" | LC_ALL=C sort)
expected_members=$(printf '%s\n' \
	SHA256SUMS \
	apk-installed-manifest.txt \
	created-at-utc.txt \
	dendelion-sysupgrade.tar.gz \
	system-board.json \
	sysupgrade-file-list.txt | LC_ALL=C sort)
[ "$actual_members" = "$expected_members" ] \
	|| fail "backup bundle has missing, duplicate, or unexpected members"
tar -xzf "$backup_bundle" -C "$work"
if command -v sha256sum >/dev/null 2>&1; then
	(cd "$work" && sha256sum -c SHA256SUMS >/dev/null) \
		|| fail "backup checksum verification failed"
else
	(cd "$work" && shasum -a 256 -c SHA256SUMS >/dev/null) \
		|| fail "backup checksum verification failed"
fi
jq -e --arg board "$OPENWRT_BOARD" --arg release "$OPENWRT_RELEASE" \
	'(.board_name == $board) and (.release.version == $release)' \
	"$work/system-board.json" >/dev/null \
	|| fail "backup is not from $OPENWRT_BOARD on OpenWrt $OPENWRT_RELEASE"

awk 'NF { print $1 }' "$work/apk-installed-manifest.txt" \
	| LC_ALL=C sort -u > "$work/packages.txt"
[ -s "$work/packages.txt" ] || fail "backup package manifest is empty"
while IFS= read -r package; do
	case $package in
		*[!A-Za-z0-9+_.-]*) fail "unsafe package name in backup: $package" ;;
	esac
done < "$work/packages.txt"
mkdir -p "$(dirname "$output_file")"
install -m 0600 "$work/packages.txt" "$output_file"
printf 'Wrote %s package names to %s\n' \
	"$(wc -l < "$output_file" | tr -d '[:space:]')" "$output_file"
