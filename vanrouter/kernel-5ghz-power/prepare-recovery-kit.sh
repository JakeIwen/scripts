#!/bin/bash
# Assemble a private, self-verifying recovery directory. It deliberately
# includes both the normal official sysupgrade image and the initramfs recovery
# image, but never includes bootloader-writing installer artifacts.
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
# shellcheck source=release.conf
. "$script_dir/release.conf"

manifest_version_parser="$script_dir/manifest-package-version.awk"

patched_image=
backup_bundle=
output_dir=
lock_dir=

usage() {
	cat <<EOF
Usage: $0 --patched-image IMAGE --backup BUNDLE --output NEW_DIRECTORY

IMAGE must be an artifact emitted by build-openwrt.sh, with BUILD-INFO.txt in
the same directory. BUNDLE must be vanpi's verified dendelion-latest.tar.gz.
NEW_DIRECTORY must not already exist.
EOF
}

fail() {
	printf 'prepare-recovery-kit: %s\n' "$1" >&2
	exit 1
}

sha256_file() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | awk '{print $1}'
	else
		shasum -a 256 "$1" | awk '{print $1}'
	fi
}

verify_sha256_file() {
	local directory=$1 sums=$2
	if command -v sha256sum >/dev/null 2>&1; then
		(cd "$directory" && sha256sum -c "$sums")
	else
		(cd "$directory" && shasum -a 256 -c "$sums")
	fi
}

build_info_value() {
	local key=$1 count value
	count=$(grep -c "^$key=" "$build_info" || true)
	[ "$count" -eq 1 ] || fail "BUILD-INFO.txt must contain $key exactly once"
	value=$(sed -n "s/^$key=//p" "$build_info")
	[ -n "$value" ] || fail "BUILD-INFO.txt has an empty $key"
	printf '%s\n' "$value"
}

artifact_sum_value() {
	local filename=$1
	awk -v filename="$filename" '
	{
		listed = $2
		sub(/^\*/, "", listed)
		if (listed == filename) {
			count++
			hash = $1
		}
	}
	END {
		if (count == 1)
			print hash
	}' "$artifact_sums"
}

while [ "$#" -gt 0 ]; do
	case $1 in
		--patched-image)
			[ "$#" -ge 2 ] || { usage >&2; exit 2; }
			patched_image=$2
			shift 2
			;;
		--backup)
			[ "$#" -ge 2 ] || { usage >&2; exit 2; }
			backup_bundle=$2
			shift 2
			;;
		--output)
			[ "$#" -ge 2 ] || { usage >&2; exit 2; }
			output_dir=$2
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

[ -r "$patched_image" ] || fail "patched image is unreadable: $patched_image"
[ -r "$backup_bundle" ] || fail "backup bundle is unreadable: $backup_bundle"
[ -n "$output_dir" ] || { usage >&2; exit 2; }
[ ! -e "$output_dir" ] || fail "output already exists: $output_dir"

for command_name in curl jq tar mkdir rmdir; do
	command -v "$command_name" >/dev/null 2>&1 \
		|| fail "required command is missing: $command_name"
done

artifact_dir=$(dirname "$patched_image")
build_info="$artifact_dir/BUILD-INFO.txt"
artifact_sums="$artifact_dir/SHA256SUMS"
[ -r "$build_info" ] || fail "BUILD-INFO.txt is missing beside the patched image"
[ -r "$artifact_sums" ] || fail "artifact SHA256SUMS is missing"
[ -r "$manifest_version_parser" ] \
	|| fail "manifest package-version parser is unreadable"
patched_name=$(basename "$patched_image")
case $patched_name in
	openwrt-$OPENWRT_RELEASE-$OPENWRT_TARGET-$OPENWRT_SUBTARGET-$OPENWRT_DEVICE-squashfs-sysupgrade-mac80211-s8min-fix.itb) ;;
	*) fail "unexpected patched image name: $patched_name" ;;
esac
patched_hash=$(sha256_file "$patched_image")
patch_hash=$(sha256_file \
	"$script_dir/patches/999-mac80211-ignore-s8-min-country-power.patch")
for provenance_name in BUILD-INFO.txt FWTOOL-METADATA.json \
	IMAGE-MANIFEST.txt PACKAGE-MAP.tsv PACKAGES-RESOLVED.txt \
	SOURCE-SHA256SUMS config.buildinfo feeds.buildinfo profiles.json \
	version.buildinfo "$patched_name"; do
	[ -f "$artifact_dir/$provenance_name" ] \
		&& [ ! -L "$artifact_dir/$provenance_name" ] \
		|| fail "required artifact is missing or a symlink: $provenance_name"
	listed_hash=$(artifact_sum_value "$provenance_name")
	[ -n "$listed_hash" ] \
		|| fail "artifact SHA256SUMS does not uniquely list $provenance_name"
	[ "$listed_hash" = "$(sha256_file "$artifact_dir/$provenance_name")" ] \
		|| fail "artifact SHA-256 mismatch: $provenance_name"
done
verify_sha256_file "$artifact_dir" SHA256SUMS >/dev/null \
	|| fail "artifact checksum verification failed"
[ "$(build_info_value OPENWRT_RELEASE)" = "$OPENWRT_RELEASE" ] \
	|| fail "BUILD-INFO.txt has the wrong release"
[ "$(build_info_value OPENWRT_SOURCE_COMMIT)" = "$OPENWRT_SOURCE_COMMIT" ] \
	|| fail "BUILD-INFO.txt has the wrong source commit"
[ "$(build_info_value OPENWRT_REVISION)" = "$OPENWRT_REVISION" ] \
	|| fail "BUILD-INFO.txt has the wrong revision"
[ "$(build_info_value OPENWRT_BASE_FILES_COMMITCOUNT)" = \
	"$OPENWRT_BASE_FILES_COMMITCOUNT" ] \
	|| fail "BUILD-INFO.txt has the wrong base-files history count"
[ "$(build_info_value OPENWRT_BASE_FILES_VERSION)" = \
	"$OPENWRT_BASE_FILES_VERSION" ] \
	|| fail "BUILD-INFO.txt has the wrong base-files version"
[ "$(build_info_value OPENWRT_TARGET)" = "$OPENWRT_TARGET" ] \
	|| fail "BUILD-INFO.txt has the wrong target"
[ "$(build_info_value OPENWRT_SUBTARGET)" = "$OPENWRT_SUBTARGET" ] \
	|| fail "BUILD-INFO.txt has the wrong subtarget"
[ "$(build_info_value OPENWRT_DEVICE)" = "$OPENWRT_DEVICE" ] \
	|| fail "BUILD-INFO.txt has the wrong device"
[ "$(build_info_value OPENWRT_BOARD)" = "$OPENWRT_BOARD" ] \
	|| fail "BUILD-INFO.txt has the wrong board"
[ "$(build_info_value OPENWRT_COMPAT_VERSION)" = "$OPENWRT_COMPAT_VERSION" ] \
	|| fail "BUILD-INFO.txt has the wrong compatibility version"
[ "$(build_info_value OPENWRT_FEEDS_BUILDINFO_SHA256)" = \
	"$OPENWRT_FEEDS_BUILDINFO_SHA256" ] \
	|| fail "BUILD-INFO.txt has the wrong feed provenance hash"
[ "$(sha256_file "$artifact_dir/feeds.buildinfo")" = \
	"$OPENWRT_FEEDS_BUILDINFO_SHA256" ] \
	|| fail "feeds.buildinfo differs from the pinned release feeds"
[ "$(build_info_value PATCH_SHA256)" = "$patch_hash" ] \
	|| fail "BUILD-INFO.txt has the wrong patch hash"
[ "$(build_info_value IMAGE)" = "$patched_name" ] \
	|| fail "BUILD-INFO.txt has the wrong image name"
[ "$(build_info_value IMAGE_SHA256)" = "$patched_hash" ] \
	|| fail "BUILD-INFO.txt does not match the patched image"
[ "$(build_info_value IMAGE_SIGNED)" = no ] \
	|| fail "BUILD-INFO.txt has an unexpected signing state"
base_files_version=$(awk -v package_name=base-files \
	-f "$manifest_version_parser" "$artifact_dir/IMAGE-MANIFEST.txt") \
	|| fail "image manifest must contain exactly one well-formed base-files record"
[ "$base_files_version" = "$OPENWRT_BASE_FILES_VERSION" ] \
	|| fail "image base-files version is $base_files_version, expected $OPENWRT_BASE_FILES_VERSION"
kernel_version=$(awk -v package_name=kernel \
	-f "$manifest_version_parser" "$artifact_dir/IMAGE-MANIFEST.txt") \
	|| fail "image manifest must contain exactly one well-formed kernel record"
[ "$(build_info_value KERNEL_PACKAGE_VERSION)" = "$kernel_version" ] \
	|| fail "BUILD-INFO.txt kernel version differs from the image manifest"
jq -e --arg release "$OPENWRT_RELEASE" --arg revision "$OPENWRT_REVISION" \
	--arg target "$OPENWRT_TARGET/$OPENWRT_SUBTARGET" \
	--arg device "$OPENWRT_DEVICE" --arg board "$OPENWRT_BOARD" \
	--arg compat "$OPENWRT_COMPAT_VERSION" '
	(.metadata_version == "1.1") and (.compat_version == $compat) and
	(.version.version == $release) and (.version.revision == $revision) and
	(.version.target == $target) and (.version.board == $device) and
	((.new_supported_devices // []) | index($board) != null)
	' "$artifact_dir/FWTOOL-METADATA.json" >/dev/null \
	|| fail "artifact sysupgrade metadata is inconsistent"

umask 077
verify_dir=$(mktemp -d "${TMPDIR:-/tmp}/dendelion-kit-verify.XXXXXX")
case $verify_dir in
	"${TMPDIR:-/tmp}"/dendelion-kit-verify.*) ;;
	*) fail "refusing unexpected verification path: $verify_dir" ;;
esac
stage_dir=
cleanup() {
	rm -rf "$verify_dir"
	[ -z "$stage_dir" ] || rm -rf "$stage_dir"
	if [ -n "$lock_dir" ] && [ -d "$lock_dir" ]; then
		case $lock_dir in
			"$output_parent"/."$output_base".prepare.lock) rmdir "$lock_dir" ;;
		esac
	fi
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
tar -xzf "$backup_bundle" -C "$verify_dir"
verify_sha256_file "$verify_dir" SHA256SUMS >/dev/null \
	|| fail "backup bundle checksum verification failed"
jq -e --arg board "$OPENWRT_BOARD" --arg release "$OPENWRT_RELEASE" \
	'(.board_name == $board) and (.release.version == $release)' \
	"$verify_dir/system-board.json" >/dev/null \
	|| fail "backup bundle is not from $OPENWRT_BOARD on $OPENWRT_RELEASE"

output_parent=$(dirname "$output_dir")
mkdir -p "$output_parent"
output_parent=$(CDPATH= cd -- "$output_parent" && pwd)
output_base=$(basename "$output_dir")
case $output_base in
	''|.|..) fail "unsafe output directory name" ;;
esac
output_dir="$output_parent/$output_base"
[ ! -e "$output_dir" ] || fail "output already exists: $output_dir"
lock_dir="$output_parent/.${output_base}.prepare.lock"
mkdir "$lock_dir" 2>/dev/null \
	|| fail "another kit builder is active or left a lock: $lock_dir"
[ ! -e "$output_dir" ] || fail "output appeared while acquiring the kit-builder lock"
stage_dir=$(mktemp -d "$output_parent/.${output_base}.incoming.XXXXXX")
chmod 0700 "$stage_dir"

release_url="https://downloads.openwrt.org/releases/$OPENWRT_RELEASE/targets/$OPENWRT_TARGET/$OPENWRT_SUBTARGET"
curl -fL --retry 3 -o "$stage_dir/$OFFICIAL_SYSUPGRADE" \
	"$release_url/$OFFICIAL_SYSUPGRADE"
curl -fL --retry 3 -o "$stage_dir/$OFFICIAL_RECOVERY" \
	"$release_url/$OFFICIAL_RECOVERY"
curl -fL --retry 3 -o "$stage_dir/openwrt-sha256sums" \
	"$release_url/sha256sums"
curl -fL --retry 3 -o "$stage_dir/openwrt-sha256sums.asc" \
	"$release_url/sha256sums.asc"

[ "$(sha256_file "$stage_dir/$OFFICIAL_SYSUPGRADE")" = \
	"$OFFICIAL_SYSUPGRADE_SHA256" ] || fail "official sysupgrade checksum mismatch"
[ "$(sha256_file "$stage_dir/$OFFICIAL_RECOVERY")" = \
	"$OFFICIAL_RECOVERY_SHA256" ] || fail "official recovery checksum mismatch"
grep -F "$OFFICIAL_SYSUPGRADE_SHA256 *$OFFICIAL_SYSUPGRADE" \
	"$stage_dir/openwrt-sha256sums" >/dev/null \
	|| fail "official checksum list does not contain the pinned sysupgrade image"
grep -F "$OFFICIAL_RECOVERY_SHA256 *$OFFICIAL_RECOVERY" \
	"$stage_dir/openwrt-sha256sums" >/dev/null \
	|| fail "official checksum list does not contain the pinned recovery image"

for provenance_name in BUILD-INFO.txt FWTOOL-METADATA.json \
	IMAGE-MANIFEST.txt PACKAGE-MAP.tsv PACKAGES-RESOLVED.txt \
	SOURCE-SHA256SUMS config.buildinfo feeds.buildinfo profiles.json \
	version.buildinfo; do
	install -m 0600 "$artifact_dir/$provenance_name" \
		"$stage_dir/$provenance_name"
done
install -m 0600 "$artifact_sums" "$stage_dir/BUILD-SHA256SUMS"
install -m 0600 "$patched_image" "$stage_dir/$patched_name"
install -m 0600 "$backup_bundle" "$stage_dir/dendelion-latest.tar.gz"
install -m 0700 "$script_dir/image-action-from-vanpi.sh" \
	"$stage_dir/image-action-from-vanpi.sh"
install -m 0600 "$script_dir/release.conf" "$stage_dir/release.conf"

{
	printf 'OPENWRT_RELEASE=%s\n' "$OPENWRT_RELEASE"
	printf 'OPENWRT_SOURCE_COMMIT=%s\n' "$OPENWRT_SOURCE_COMMIT"
	printf 'OPENWRT_BASE_FILES_COMMITCOUNT=%s\n' \
		"$OPENWRT_BASE_FILES_COMMITCOUNT"
	printf 'OPENWRT_BASE_FILES_VERSION=%s\n' \
		"$OPENWRT_BASE_FILES_VERSION"
	printf 'KERNEL_PACKAGE_VERSION=%s\n' "$kernel_version"
	printf 'OPENWRT_BOARD=%s\n' "$OPENWRT_BOARD"
	printf 'OPENWRT_COMPAT_VERSION=%s\n' "$OPENWRT_COMPAT_VERSION"
	printf 'PATCHED_IMAGE=%s\n' "$patched_name"
	printf 'PATCHED_IMAGE_SHA256=%s\n' "$patched_hash"
	printf 'OFFICIAL_SYSUPGRADE=%s\n' "$OFFICIAL_SYSUPGRADE"
	printf 'OFFICIAL_SYSUPGRADE_SHA256=%s\n' "$OFFICIAL_SYSUPGRADE_SHA256"
	printf 'OFFICIAL_RECOVERY=%s\n' "$OFFICIAL_RECOVERY"
	printf 'OFFICIAL_RECOVERY_SHA256=%s\n' "$OFFICIAL_RECOVERY_SHA256"
	printf 'BACKUP_SHA256=%s\n' "$(sha256_file "$backup_bundle")"
} > "$stage_dir/KIT-INFO.txt"

(
	cd "$stage_dir"
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum BUILD-INFO.txt BUILD-SHA256SUMS FWTOOL-METADATA.json \
			IMAGE-MANIFEST.txt KIT-INFO.txt PACKAGE-MAP.tsv \
			PACKAGES-RESOLVED.txt SOURCE-SHA256SUMS config.buildinfo \
			dendelion-latest.tar.gz feeds.buildinfo image-action-from-vanpi.sh \
			openwrt-sha256sums \
			openwrt-sha256sums.asc release.conf "$patched_name" \
			profiles.json version.buildinfo "$OFFICIAL_SYSUPGRADE" \
			"$OFFICIAL_RECOVERY" > SHA256SUMS
	else
		shasum -a 256 BUILD-INFO.txt BUILD-SHA256SUMS \
			FWTOOL-METADATA.json IMAGE-MANIFEST.txt KIT-INFO.txt \
			PACKAGE-MAP.tsv PACKAGES-RESOLVED.txt SOURCE-SHA256SUMS \
			config.buildinfo dendelion-latest.tar.gz feeds.buildinfo \
			image-action-from-vanpi.sh openwrt-sha256sums \
			openwrt-sha256sums.asc release.conf "$patched_name" \
			profiles.json version.buildinfo "$OFFICIAL_SYSUPGRADE" \
			"$OFFICIAL_RECOVERY" > SHA256SUMS
	fi
)
mv "$stage_dir" "$output_dir"
stage_dir=
rmdir "$lock_dir"
lock_dir=
printf 'Prepared private recovery kit: %s\n' "$output_dir"
