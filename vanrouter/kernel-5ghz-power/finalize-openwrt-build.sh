#!/bin/bash
# Validate an already completed OpenWrt build and atomically emit the custom
# sysupgrade artifact plus its build provenance. This script never runs make.
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
# shellcheck source=release.conf
. "$script_dir/release.conf"

patch_relative=package/kernel/mac80211/patches/subsys/999-mac80211-ignore-s8-min-country-power.patch
patch_file="$script_dir/patches/999-mac80211-ignore-s8-min-country-power.patch"
package_resolver="$script_dir/resolve-package-seed.awk"
profile_selector="$script_dir/select-sysupgrade-image.jq"
manifest_version_parser="$script_dir/manifest-package-version.awk"
output_dir=
work_dir=
stage_dir=
lock_dir=

usage() {
	printf 'Usage: %s --output COMPLETED_BUILD_DIRECTORY\n' "$0"
}

fail() {
	printf 'finalize-openwrt-build: %s\n' "$1" >&2
	exit 1
}

sha256_file() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | awk '{print $1}'
	else
		shasum -a 256 "$1" | awk '{print $1}'
	fi
}

cleanup() {
	if [ -n "$work_dir" ] && [ -d "$work_dir" ]; then
		case $work_dir in
			"${TMPDIR:-/tmp}"/dendelion-finalize.*) rm -rf "$work_dir" ;;
		esac
	fi
	if [ -n "$stage_dir" ] && [ -d "$stage_dir" ]; then
		case $stage_dir in
			"$output_dir"/.artifacts.incoming.*) rm -rf "$stage_dir" ;;
		esac
	fi
	if [ -n "$lock_dir" ] && [ -d "$lock_dir" ]; then
		case $lock_dir in
			"$output_dir"/.artifacts.finalize.lock) rmdir "$lock_dir" ;;
		esac
	fi
}
trap cleanup EXIT HUP INT TERM

while [ "$#" -gt 0 ]; do
	case $1 in
		--output)
			[ "$#" -ge 2 ] || { usage >&2; exit 2; }
			output_dir=$2
			shift 2
			;;
		-h|--help)
			usage
			exit 0
			;;
		*) usage >&2; exit 2 ;;
	esac
done

[ -n "$output_dir" ] || { usage >&2; exit 2; }
[ -d "$output_dir" ] || fail "build directory does not exist: $output_dir"
output_dir=$(CDPATH= cd -- "$output_dir" && pwd)
build_source="$output_dir/source"
target_dir="$build_source/bin/targets/$OPENWRT_TARGET/$OPENWRT_SUBTARGET"
artifacts="$output_dir/artifacts"
[ -d "$build_source/.git" ] || fail "build source is not a Git checkout"
[ -d "$target_dir" ] || fail "target output directory is missing"
[ ! -e "$artifacts" ] || fail "artifacts already exist: $artifacts"
[ -r "$patch_file" ] || fail "toolkit patch is unreadable"
[ -r "$package_resolver" ] || fail "package resolver is unreadable"
[ -r "$profile_selector" ] || fail "profile selector is unreadable"
[ -r "$manifest_version_parser" ] \
	|| fail "manifest package-version parser is unreadable"

for command_name in git jq awk grep cmp sort comm cut install mktemp mkdir rmdir; do
	command -v "$command_name" >/dev/null 2>&1 \
		|| fail "required command is missing: $command_name"
done
lock_dir="$output_dir/.artifacts.finalize.lock"
mkdir "$lock_dir" 2>/dev/null \
	|| fail "another finalizer is active or left a lock: $lock_dir"
[ ! -e "$artifacts" ] || fail "artifacts appeared while acquiring the finalizer lock"

actual_commit=$(git -C "$build_source" rev-parse HEAD)
[ "$actual_commit" = "$OPENWRT_SOURCE_COMMIT" ] \
	|| fail "source is $actual_commit, expected $OPENWRT_SOURCE_COMMIT"
is_shallow=$(git -C "$build_source" rev-parse --is-shallow-repository) \
	|| fail "could not determine whether build source history is shallow"
[ "$is_shallow" = false ] \
	|| fail "build source is shallow; complete Git history is required"
base_files_commitcount=$(git -C "$build_source" rev-list --count HEAD \
	-- package/base-files) \
	|| fail "could not count package/base-files history"
[ "$base_files_commitcount" = "$OPENWRT_BASE_FILES_COMMITCOUNT" ] \
	|| fail "package/base-files history count is $base_files_commitcount, expected $OPENWRT_BASE_FILES_COMMITCOUNT"
git -C "$build_source" diff --quiet -- \
	|| fail "build source has tracked working-tree changes"
git -C "$build_source" diff --cached --quiet -- \
	|| fail "build source has staged changes"
untracked=$(git -C "$build_source" ls-files --others --exclude-standard)
[ "$untracked" = "$patch_relative" ] \
	|| fail "build source has unexpected untracked files"
cmp "$patch_file" "$build_source/$patch_relative" >/dev/null \
	|| fail "build-source patch differs from the toolkit patch"

config="$build_source/.config"
config_buildinfo="$target_dir/config.buildinfo"
feeds_buildinfo="$target_dir/feeds.buildinfo"
version_buildinfo="$target_dir/version.buildinfo"
profiles="$target_dir/profiles.json"
source_sums="$target_dir/sha256sums"
resolved_packages="$output_dir/PACKAGES-RESOLVED.txt"
package_map="$output_dir/PACKAGE-MAP.tsv"
package_metadata="$build_source/tmp/.packageinfo"
for required_file in "$config" "$config_buildinfo" "$feeds_buildinfo" \
	"$version_buildinfo" "$profiles" "$source_sums" "$resolved_packages" \
	"$package_map" "$package_metadata"; do
	[ -s "$required_file" ] || fail "required build output is missing: $required_file"
done
grep -Fx 'CONFIG_TARGET_mediatek_mt7622=y' "$config" >/dev/null \
	|| fail "build configuration has the wrong subtarget"
grep -Fx "CONFIG_TARGET_mediatek_mt7622_DEVICE_$OPENWRT_DEVICE=y" \
	"$config" >/dev/null || fail "build configuration has the wrong device"
grep -Fx 'CONFIG_TARGET_mediatek_mt7622=y' "$config_buildinfo" >/dev/null \
	|| fail "config.buildinfo has the wrong subtarget"
grep -Fx "CONFIG_TARGET_mediatek_mt7622_DEVICE_$OPENWRT_DEVICE=y" \
	"$config_buildinfo" >/dev/null \
	|| fail "config.buildinfo has the wrong device"
[ "$(cat "$version_buildinfo")" = "$OPENWRT_REVISION" ] \
	|| fail "version.buildinfo has the wrong revision"
[ "$(sha256_file "$feeds_buildinfo")" = "$OPENWRT_FEEDS_BUILDINFO_SHA256" ] \
	|| fail "feeds.buildinfo differs from the pinned release feeds"
jq -e --arg target "$OPENWRT_TARGET/$OPENWRT_SUBTARGET" \
	--arg release "$OPENWRT_RELEASE" --arg revision "$OPENWRT_REVISION" \
	'(.target == $target) and (.version_number == $release) and
	 (.version_code == $revision)' "$profiles" >/dev/null \
	|| fail "profiles.json has the wrong target or version"

selection=$(jq -er --arg profile "$OPENWRT_DEVICE" \
	--arg board "$OPENWRT_BOARD" -f "$profile_selector" "$profiles") \
	|| fail "profiles.json does not identify one safe sysupgrade image"
[ "$(printf '%s\n' "$selection" | wc -l | tr -d '[:space:]')" = 1 ] \
	|| fail "profile selector returned multiple records"
IFS=$'\t' read -r source_name expected_hash expected_size <<< "$selection"
case $source_name in
	''|*[!A-Za-z0-9_.+-]*) fail "unsafe source image name" ;;
esac
image="$target_dir/$source_name"
[ -f "$image" ] && [ ! -L "$image" ] && [ -s "$image" ] \
	|| fail "profile-selected image is missing, empty, or a symlink"
[ "$(stat -c %s "$image")" = "$expected_size" ] \
	|| fail "profile-selected image size does not match profiles.json"
actual_hash=$(sha256_file "$image")
[ "$actual_hash" = "$expected_hash" ] \
	|| fail "profile-selected image hash does not match profiles.json"
listed_hash=$(awk -v name="$source_name" '
	{
		listed = $2
		sub(/^\*/, "", listed)
		if (listed == name) {
			count++
			hash = $1
		}
	}
	END {
		if (count == 1)
			print hash
	}' "$source_sums")
[ "$listed_hash" = "$actual_hash" ] \
	|| fail "sha256sums does not uniquely match the selected image"
jq -e --arg profile "$OPENWRT_DEVICE" '
	.profiles[$profile].images
	| map(select(.type == "sysupgrade" and .filesystem == "squashfs"))
	| (length == 1) and (.[0].sha256_unsigned == .[0].sha256)
	' "$profiles" >/dev/null \
	|| fail "selected image has unexpected signing metadata"

manifest_paths=$(find "$target_dir" -maxdepth 1 -type f \
	-name "*-$OPENWRT_DEVICE.manifest" -print)
[ "$(printf '%s\n' "$manifest_paths" | sed '/^$/d' | wc -l | tr -d '[:space:]')" = 1 ] \
	|| fail "expected exactly one device package manifest"
manifest=$manifest_paths
base_files_version=$(awk -v package_name=base-files \
	-f "$manifest_version_parser" "$manifest") \
	|| fail "image manifest must contain exactly one well-formed base-files record"
[ "$base_files_version" = "$OPENWRT_BASE_FILES_VERSION" ] \
	|| fail "image base-files version is $base_files_version, expected $OPENWRT_BASE_FILES_VERSION"
kernel_version_paths=$(find "$build_source/staging_dir" -mindepth 2 \
	-maxdepth 2 -type f -name kernel.version -print)
[ "$(printf '%s\n' "$kernel_version_paths" | sed '/^$/d' | wc -l | tr -d '[:space:]')" = 1 ] \
	|| fail "expected exactly one generated kernel.version file"
kernel_version=$(cat "$kernel_version_paths")
[ -n "$kernel_version" ] || fail "generated kernel.version is empty"
manifest_kernel_version=$(awk -v package_name=kernel \
	-f "$manifest_version_parser" "$manifest") \
	|| fail "image manifest must contain exactly one well-formed kernel record"
[ "$manifest_kernel_version" = "$kernel_version" ] \
	|| fail "image kernel version differs from the generated kernel.version"

work_dir=$(mktemp -d "${TMPDIR:-/tmp}/dendelion-finalize.XXXXXX")
case $work_dir in
	"${TMPDIR:-/tmp}"/dendelion-finalize.*) ;;
	*) fail "refusing unexpected temporary path: $work_dir" ;;
esac
fwtool="$build_source/staging_dir/host/bin/fwtool"
[ -x "$fwtool" ] || fail "built fwtool is missing"
install -m 0600 "$image" "$work_dir/image.itb"
"$fwtool" -i "$work_dir/FWTOOL-METADATA.json" \
	"$work_dir/image.itb" >/dev/null \
	|| fail "could not extract sysupgrade metadata from a protected image copy"
[ -s "$work_dir/FWTOOL-METADATA.json" ] \
	|| fail "sysupgrade metadata is empty"
jq -e --arg release "$OPENWRT_RELEASE" --arg revision "$OPENWRT_REVISION" \
	--arg target "$OPENWRT_TARGET/$OPENWRT_SUBTARGET" \
	--arg device "$OPENWRT_DEVICE" --arg board "$OPENWRT_BOARD" \
	--arg compat "$OPENWRT_COMPAT_VERSION" '
	(.metadata_version == "1.1") and (.compat_version == $compat) and
	(.version.dist == "OpenWrt") and (.version.version == $release) and
	(.version.revision == $revision) and (.version.target == $target) and
	(.version.board == $device) and
	((.new_supported_devices // []) | index($board) != null)
	' "$work_dir/FWTOOL-METADATA.json" >/dev/null \
	|| fail "sysupgrade metadata has the wrong release, target, or compatibility"
if "$fwtool" -q -s "$work_dir/FWTOOL-SIGNATURE" \
	"$work_dir/image.itb"; then
	fail "sysupgrade image unexpectedly contains an appended signature"
fi
[ ! -s "$work_dir/FWTOOL-SIGNATURE" ] \
	|| fail "sysupgrade signature extraction produced unexpected data"
[ "$(sha256_file "$work_dir/image.itb")" = "$actual_hash" ] \
	|| fail "metadata inspection modified the protected image copy"
[ "$(sha256_file "$image")" = "$actual_hash" ] \
	|| fail "source image changed during finalization"

awk 'NF { print $1 }' "$manifest" > "$work_dir/runtime-packages.txt"
: > "$work_dir/package-map-unused.tsv"
awk -v metadata_file="$package_metadata" \
	-v map_file="$work_dir/package-map-unused.tsv" \
	-f "$package_resolver" "$package_metadata" \
	"$work_dir/runtime-packages.txt" > "$work_dir/image-selectors.txt" \
	2> "$work_dir/package-resolution.log" \
	|| fail "image manifest contains an unresolvable package"
LC_ALL=C sort -u "$resolved_packages" > "$work_dir/expected-selectors.txt"
LC_ALL=C sort -u "$work_dir/image-selectors.txt" \
	> "$work_dir/actual-selectors.txt"
if ! cmp "$work_dir/expected-selectors.txt" \
	"$work_dir/actual-selectors.txt" >/dev/null; then
	printf 'Package selector differences:\n' >&2
	comm -3 "$work_dir/expected-selectors.txt" \
		"$work_dir/actual-selectors.txt" >&2 || true
	fail "image package manifest differs from the requested package set"
fi

stage_dir=$(mktemp -d "$output_dir/.artifacts.incoming.XXXXXX")
case $stage_dir in
	"$output_dir"/.artifacts.incoming.*) ;;
	*) fail "refusing unexpected artifact staging path: $stage_dir" ;;
esac
patched_name="openwrt-$OPENWRT_RELEASE-$OPENWRT_TARGET-$OPENWRT_SUBTARGET-$OPENWRT_DEVICE-squashfs-sysupgrade-mac80211-s8min-fix.itb"
install -m 0644 "$work_dir/image.itb" "$stage_dir/$patched_name"
[ "$(sha256_file "$stage_dir/$patched_name")" = "$actual_hash" ] \
	|| fail "staged artifact hash differs from the validated image"
[ "$(sha256_file "$image")" = "$actual_hash" ] \
	|| fail "source image changed before artifact staging"
install -m 0644 "$resolved_packages" "$stage_dir/PACKAGES-RESOLVED.txt"
install -m 0644 "$package_map" "$stage_dir/PACKAGE-MAP.tsv"
install -m 0644 "$config_buildinfo" "$stage_dir/config.buildinfo"
install -m 0644 "$feeds_buildinfo" "$stage_dir/feeds.buildinfo"
install -m 0644 "$version_buildinfo" "$stage_dir/version.buildinfo"
install -m 0644 "$profiles" "$stage_dir/profiles.json"
install -m 0644 "$source_sums" "$stage_dir/SOURCE-SHA256SUMS"
install -m 0644 "$manifest" "$stage_dir/IMAGE-MANIFEST.txt"
install -m 0644 "$work_dir/FWTOOL-METADATA.json" \
	"$stage_dir/FWTOOL-METADATA.json"
{
	printf 'OPENWRT_RELEASE=%s\n' "$OPENWRT_RELEASE"
	printf 'OPENWRT_SOURCE_COMMIT=%s\n' "$OPENWRT_SOURCE_COMMIT"
	printf 'OPENWRT_REVISION=%s\n' "$OPENWRT_REVISION"
	printf 'OPENWRT_BASE_FILES_COMMITCOUNT=%s\n' \
		"$OPENWRT_BASE_FILES_COMMITCOUNT"
	printf 'OPENWRT_BASE_FILES_VERSION=%s\n' \
		"$OPENWRT_BASE_FILES_VERSION"
	printf 'KERNEL_PACKAGE_VERSION=%s\n' "$kernel_version"
	printf 'OPENWRT_TARGET=%s\n' "$OPENWRT_TARGET"
	printf 'OPENWRT_SUBTARGET=%s\n' "$OPENWRT_SUBTARGET"
	printf 'OPENWRT_DEVICE=%s\n' "$OPENWRT_DEVICE"
	printf 'OPENWRT_BOARD=%s\n' "$OPENWRT_BOARD"
	printf 'OPENWRT_COMPAT_VERSION=%s\n' "$OPENWRT_COMPAT_VERSION"
	printf 'OPENWRT_FEEDS_BUILDINFO_SHA256=%s\n' \
		"$OPENWRT_FEEDS_BUILDINFO_SHA256"
	printf 'MAC80211_BACKPORTS_VERSION=%s\n' "$MAC80211_BACKPORTS_VERSION"
	printf 'PATCH_SHA256=%s\n' "$(sha256_file "$patch_file")"
	printf 'SOURCE_IMAGE=%s\n' "$source_name"
	printf 'SOURCE_IMAGE_SHA256=%s\n' "$actual_hash"
	printf 'SOURCE_IMAGE_SIZE=%s\n' "$expected_size"
	printf 'IMAGE=%s\n' "$patched_name"
	printf 'IMAGE_SHA256=%s\n' "$actual_hash"
	printf 'IMAGE_SIGNED=no\n'
	printf 'PACKAGES_RESOLVED_SHA256=%s\n' \
		"$(sha256_file "$stage_dir/PACKAGES-RESOLVED.txt")"
	printf 'PACKAGE_MAP_SHA256=%s\n' \
		"$(sha256_file "$stage_dir/PACKAGE-MAP.tsv")"
	printf 'PROFILES_JSON_SHA256=%s\n' \
		"$(sha256_file "$stage_dir/profiles.json")"
	printf 'FWTOOL_METADATA_SHA256=%s\n' \
		"$(sha256_file "$stage_dir/FWTOOL-METADATA.json")"
	printf 'IMAGE_MANIFEST_SHA256=%s\n' \
		"$(sha256_file "$stage_dir/IMAGE-MANIFEST.txt")"
} > "$stage_dir/BUILD-INFO.txt"
(
	cd "$stage_dir"
	sha256sum BUILD-INFO.txt FWTOOL-METADATA.json IMAGE-MANIFEST.txt \
		PACKAGE-MAP.tsv PACKAGES-RESOLVED.txt SOURCE-SHA256SUMS \
		config.buildinfo feeds.buildinfo profiles.json version.buildinfo \
		"$patched_name" > SHA256SUMS
)
mv "$stage_dir" "$artifacts"
stage_dir=
rmdir "$lock_dir"
lock_dir=
printf 'Finalized patched image: %s\n' "$artifacts/$patched_name"
printf 'SHA-256: %s\n' "$actual_hash"
