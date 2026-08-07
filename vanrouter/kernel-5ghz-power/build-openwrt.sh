#!/bin/bash
# Build an E8450 UBI image containing the local mac80211 patch. The supplied
# OpenWrt checkout is treated as an immutable seed; all changes happen in a
# fresh output directory.
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
# shellcheck source=release.conf
. "$script_dir/release.conf"

patch_file="$script_dir/patches/999-mac80211-ignore-s8-min-country-power.patch"
package_resolver="$script_dir/resolve-package-seed.awk"
finalizer="$script_dir/finalize-openwrt-build.sh"
source_repo=
output_dir=
packages_file=
jobs=
mode=build

usage() {
	cat <<EOF
Usage:
  $0 --check-source OPENWRT_SOURCE
  $0 --source OPENWRT_SOURCE --output EMPTY_DIR [--packages FILE] [--jobs N]

The full build must run on Linux. FILE is an optional list of additional
OpenWrt package names, one per line; blank lines and # comments are ignored.
The input checkout must be clean and contain commit $OPENWRT_SOURCE_COMMIT.
EOF
}

fail() {
	printf 'build-openwrt: %s\n' "$1" >&2
	exit 1
}

sha256_file() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | awk '{print $1}'
	else
		shasum -a 256 "$1" | awk '{print $1}'
	fi
}

validate_source() {
	local source=$1 actual_commit version source_hash
	[ -d "$source/.git" ] || fail "not an OpenWrt Git checkout: $source"
	actual_commit=$(git -C "$source" rev-parse HEAD)
	[ "$actual_commit" = "$OPENWRT_SOURCE_COMMIT" ] \
		|| fail "source is $actual_commit, expected $OPENWRT_SOURCE_COMMIT"
	[ -z "$(git -C "$source" status --porcelain)" ] \
		|| fail "source checkout is not clean"
	version=$(sed -n 's/^PKG_VERSION:=//p' \
		"$source/package/kernel/mac80211/Makefile")
	source_hash=$(sed -n 's/^PKG_HASH:=//p' \
		"$source/package/kernel/mac80211/Makefile")
	[ "$version" = "$MAC80211_BACKPORTS_VERSION" ] \
		|| fail "mac80211 backports is $version, expected $MAC80211_BACKPORTS_VERSION"
	[ "$source_hash" = "$MAC80211_BACKPORTS_SHA256" ] \
		|| fail "mac80211 source hash differs from the pinned release"
	grep -F "VERSION_NUMBER),$OPENWRT_RELEASE" \
		"$source/include/version.mk" >/dev/null \
		|| fail "source does not declare OpenWrt $OPENWRT_RELEASE"
	grep -F "VERSION_CODE),$OPENWRT_REVISION" \
		"$source/include/version.mk" >/dev/null \
		|| fail "source does not declare revision $OPENWRT_REVISION"
}

check_patch_against_backports() {
	local openwrt_source=$1 work archive source_url actual_hash existing_patch
	work=$(mktemp -d "${TMPDIR:-/tmp}/dendelion-mac80211-check.XXXXXX")
	case $work in
		"${TMPDIR:-/tmp}"/dendelion-mac80211-check.*) ;;
		*) fail "refusing unexpected temporary path: $work" ;;
	esac
	trap 'rm -rf "$work"' RETURN
	archive="$work/backports-$MAC80211_BACKPORTS_VERSION.tar.zst"
	source_url="https://github.com/openwrt/backports/releases/download/backports-v$MAC80211_BACKPORTS_VERSION/backports-$MAC80211_BACKPORTS_VERSION.tar.zst"
	curl -fL --retry 3 -o "$archive" "$source_url"
	actual_hash=$(sha256_file "$archive")
	[ "$actual_hash" = "$MAC80211_BACKPORTS_SHA256" ] \
		|| fail "backports archive checksum mismatch"
	mkdir "$work/source"
	tar --zstd -xf "$archive" -C "$work/source" --strip-components=1
	(
		cd "$work/source"
		while IFS= read -r existing_patch; do
			patch --batch --fuzz=0 -p1 < "$existing_patch" >/dev/null
		done < <(find \
			"$openwrt_source/package/kernel/mac80211/patches/subsys" \
			-maxdepth 1 -type f -print | LC_ALL=C sort)
		patch --dry-run --fuzz=0 -p1 < "$patch_file" >/dev/null
		patch --fuzz=0 -p1 < "$patch_file" >/dev/null
		grep -F 'triplet->chans.max_power == S8_MIN' \
			net/mac80211/mlme.c >/dev/null
	)
	rm -rf "$work"
	trap - RETURN
}

while [ "$#" -gt 0 ]; do
	case $1 in
		--check-source)
			[ "$#" -ge 2 ] || { usage >&2; exit 2; }
			mode=check
			source_repo=$2
			shift 2
			;;
		--source)
			[ "$#" -ge 2 ] || { usage >&2; exit 2; }
			source_repo=$2
			shift 2
			;;
		--output)
			[ "$#" -ge 2 ] || { usage >&2; exit 2; }
			output_dir=$2
			shift 2
			;;
		--packages)
			[ "$#" -ge 2 ] || { usage >&2; exit 2; }
			packages_file=$2
			shift 2
			;;
		--jobs)
			[ "$#" -ge 2 ] || { usage >&2; exit 2; }
			jobs=$2
			shift 2
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			usage >&2
			exit 2
			;;
	esac
done

[ -n "$source_repo" ] || { usage >&2; exit 2; }
validate_source "$source_repo"
check_patch_against_backports "$source_repo"
[ -r "$package_resolver" ] || fail "package resolver is unreadable: $package_resolver"
[ -x "$finalizer" ] || fail "build finalizer is not executable: $finalizer"

if [ "$mode" = check ]; then
	printf 'Source and patch match OpenWrt %s (%s), mac80211 backports %s.\n' \
		"$OPENWRT_RELEASE" "$OPENWRT_SOURCE_COMMIT" \
		"$MAC80211_BACKPORTS_VERSION"
	exit 0
fi

[ "$(uname -s)" = Linux ] \
	|| fail "full OpenWrt builds require Linux; use --check-source on this host"
[ -n "$output_dir" ] || { usage >&2; exit 2; }
[ ! -e "$output_dir" ] || fail "output already exists: $output_dir"
case ${jobs:-} in
	'') jobs=$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '1\n') ;;
	*[!0-9]*|0) fail "--jobs must be a positive integer" ;;
esac
if [ -n "$packages_file" ]; then
	[ -r "$packages_file" ] || fail "package list is unreadable: $packages_file"
fi

mkdir -p "$output_dir"
output_dir=$(CDPATH= cd -- "$output_dir" && pwd)
build_source="$output_dir/source"
git clone --no-hardlinks "$source_repo" "$build_source"
git -C "$build_source" checkout --detach "$OPENWRT_SOURCE_COMMIT"
install -m 0644 "$patch_file" \
	"$build_source/package/kernel/mac80211/patches/subsys/999-mac80211-ignore-s8-min-country-power.patch"

cd "$build_source"
./scripts/feeds update -a
./scripts/feeds install -a
: > .config
{
	printf 'CONFIG_TARGET_mediatek=y\n'
	printf 'CONFIG_TARGET_mediatek_mt7622=y\n'
	printf 'CONFIG_TARGET_mediatek_mt7622_DEVICE_%s=y\n' \
		"$OPENWRT_DEVICE"
} >> .config
make defconfig

resolved_packages=
package_map=
if [ -n "$packages_file" ]; then
	[ -s tmp/.packageinfo ] \
		|| fail "OpenWrt package metadata was not generated by defconfig"
	resolved_packages="$output_dir/PACKAGES-RESOLVED.txt"
	package_map="$output_dir/PACKAGE-MAP.tsv"
	: > "$package_map"
	awk -v metadata_file="$build_source/tmp/.packageinfo" \
		-v map_file="$package_map" -f "$package_resolver" \
		"$build_source/tmp/.packageinfo" "$packages_file" \
		> "$resolved_packages" \
		|| fail "installed package seed could not be resolved"
	while IFS= read -r package || [ -n "$package" ]; do
		printf 'CONFIG_PACKAGE_%s=y\n' "$package" >> .config
	done < "$resolved_packages"
	make defconfig
fi

grep -Fx 'CONFIG_TARGET_mediatek_mt7622=y' .config >/dev/null
grep -Fx "CONFIG_TARGET_mediatek_mt7622_DEVICE_$OPENWRT_DEVICE=y" \
	.config >/dev/null
if [ -n "$resolved_packages" ]; then
	while IFS= read -r package || [ -n "$package" ]; do
		grep -Fx "CONFIG_PACKAGE_$package=y" .config >/dev/null \
			|| fail "package is unavailable in the pinned feeds: $package"
	done < "$resolved_packages"
fi

make -j"$jobs" download
if ! make -j"$jobs"; then
	printf 'Parallel build failed; retrying serially with verbose output.\n' >&2
	make -j1 V=s
fi

"$finalizer" --output "$output_dir"
