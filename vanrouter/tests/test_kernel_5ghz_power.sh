#!/bin/bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
project_dir="$script_dir/../kernel-5ghz-power"
patch_file="$project_dir/patches/999-mac80211-ignore-s8-min-country-power.patch"
test_root=$(mktemp -d "${TMPDIR:-/tmp}/kernel-5ghz-power-test.XXXXXX")
case $test_root in
	"${TMPDIR:-/tmp}"/kernel-5ghz-power-test.*) ;;
	*) printf 'unsafe test directory: %s\n' "$test_root" >&2; exit 1 ;;
esac
cleanup() {
	rm -rf "$test_root"
}
trap cleanup EXIT HUP INT TERM

for script in \
	"$project_dir/build-openwrt.sh" \
	"$project_dir/finalize-openwrt-build.sh" \
	"$project_dir/packages-from-backup.sh" \
	"$project_dir/prepare-recovery-kit.sh" \
	"$project_dir/image-action-from-vanpi.sh"; do
	/bin/bash -n "$script"
	[ -x "$script" ] || {
		printf 'script is not executable: %s\n' "$script" >&2
		exit 1
	}
done
[ -r "$project_dir/select-sysupgrade-image.jq" ] || {
	printf 'profile image selector is unreadable\n' >&2
	exit 1
}

cat > "$test_root/profiles.json" <<'EOF'
{
  "profiles": {
    "linksys_e8450-ubi": {
      "image_prefix": "openwrt-mediatek-mt7622-linksys_e8450-ubi",
      "supported_devices": ["linksys,e8450-ubi"],
      "images": [
        {
          "filesystem": "squashfs",
          "name": "openwrt-mediatek-mt7622-linksys_e8450-ubi-squashfs-sysupgrade.itb",
          "sha256": "13a57e4b902030bdacaee7b895a8f50a84a3cca618bcfc6b729a8321de5f360e",
          "size": 13398718,
          "type": "sysupgrade"
        },
        {"name": "recovery.itb", "size": 1, "type": "kernel"}
      ]
    }
  }
}
EOF
selection=$(jq -er --arg profile linksys_e8450-ubi \
	--arg board linksys,e8450-ubi \
	-f "$project_dir/select-sysupgrade-image.jq" \
	"$test_root/profiles.json")
[ "$selection" = $'openwrt-mediatek-mt7622-linksys_e8450-ubi-squashfs-sysupgrade.itb\t13a57e4b902030bdacaee7b895a8f50a84a3cca618bcfc6b729a8321de5f360e\t13398718' ]

cat > "$test_root/ambiguous-profiles.json" <<'EOF'
{
  "profiles": {
    "linksys_e8450-ubi": {
      "image_prefix": "openwrt-mediatek-mt7622-linksys_e8450-ubi",
      "supported_devices": ["linksys,e8450-ubi"],
      "images": [
        {"filesystem":"squashfs","name":"one.itb","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","size":1,"type":"sysupgrade"},
        {"filesystem":"squashfs","name":"two.itb","sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","size":2,"type":"sysupgrade"}
      ]
    }
  }
}
EOF
if jq -er --arg profile linksys_e8450-ubi \
	--arg board linksys,e8450-ubi \
	-f "$project_dir/select-sysupgrade-image.jq" \
	"$test_root/ambiguous-profiles.json" >/dev/null 2>&1; then
	printf 'expected profile selector to reject ambiguous images\n' >&2
	exit 1
fi
grep -F '"$finalizer" --output "$output_dir"' \
	"$project_dir/build-openwrt.sh" >/dev/null
if grep -F 'openwrt-$OPENWRT_RELEASE-$OPENWRT_TARGET-$OPENWRT_SUBTARGET-$OPENWRT_DEVICE-squashfs-sysupgrade.itb' \
	"$project_dir/build-openwrt.sh" >/dev/null; then
	printf 'build script still assumes versioned OpenWrt output filenames\n' >&2
	exit 1
fi
grep -F 'install -m 0644 "$work_dir/image.itb" "$stage_dir/$patched_name"' \
	"$project_dir/finalize-openwrt-build.sh" >/dev/null
grep -F 'staged artifact hash differs from the validated image' \
	"$project_dir/finalize-openwrt-build.sh" >/dev/null
grep -F '.artifacts.finalize.lock' \
	"$project_dir/finalize-openwrt-build.sh" >/dev/null
grep -F '.prepare.lock' "$project_dir/prepare-recovery-kit.sh" >/dev/null
grep -F 'trap cleanup_remote EXIT' \
	"$project_dir/image-action-from-vanpi.sh" >/dev/null
grep -F 'remote_image=/tmp/dendelion-image-action-$$.itb' \
	"$project_dir/image-action-from-vanpi.sh" >/dev/null

cat > "$test_root/packageinfo" <<'EOF'
Package: jansson
ABI-Version: 4

Package: libnghttp2
ABI-Version: 14

Package: mwan3

Package: kmod-example
ABI-Version: 99

EOF
cat > "$test_root/package-seed.txt" <<'EOF'
jansson4
kernel
libnghttp2-14
mwan3
kmod-example
jansson4
EOF
: > "$test_root/package-map.tsv"
awk -v metadata_file="$test_root/packageinfo" \
	-v map_file="$test_root/package-map.tsv" \
	-f "$project_dir/resolve-package-seed.awk" \
	"$test_root/packageinfo" "$test_root/package-seed.txt" \
	> "$test_root/packages-resolved.txt" 2> "$test_root/resolver.err"
cat > "$test_root/expected-resolved.txt" <<'EOF'
jansson
libnghttp2
mwan3
kmod-example
EOF
cmp "$test_root/expected-resolved.txt" "$test_root/packages-resolved.txt"
grep -F $'jansson4\tjansson' "$test_root/package-map.tsv" >/dev/null
grep -F $'kernel\t<implicit-target-package>' \
	"$test_root/package-map.tsv" >/dev/null
grep -F 'mapped libnghttp2-14 -> libnghttp2' \
	"$test_root/resolver.err" >/dev/null
printf 'missing-package\n' > "$test_root/missing-package-seed.txt"
if awk -v metadata_file="$test_root/packageinfo" \
	-f "$project_dir/resolve-package-seed.awk" \
	"$test_root/packageinfo" "$test_root/missing-package-seed.txt" \
	> "$test_root/missing-resolved.txt" \
	2> "$test_root/missing-resolver.err"; then
	printf 'expected package resolver to reject an unknown package\n' >&2
	exit 1
fi
grep -F 'no selector in pinned metadata: missing-package' \
	"$test_root/missing-resolver.err" >/dev/null

# shellcheck source=/dev/null
. "$project_dir/release.conf"
[ "$OPENWRT_RELEASE" = 25.12.5 ]
[ "$OPENWRT_SOURCE_COMMIT" = f0a60eee2fe051741c643ea6118718aae1ef17fb ]
[ "$OPENWRT_BOARD" = linksys,e8450-ubi ]
[ "$OPENWRT_COMPAT_VERSION" = 2.0 ]
[ "$OPENWRT_FEEDS_BUILDINFO_SHA256" = \
	e11279b01e7fea7f7d399e25e969d9382be6891071cbc1225804195224b27b52 ]
[ "$MAC80211_BACKPORTS_VERSION" = 6.18.26 ]
[ "$OFFICIAL_SYSUPGRADE_SHA256" = \
	189ef531f2e9a43b5ce2e988c4195b16f7a8eb5707bf45304a3ebcbb2bfaf9ed ]
[ "$OFFICIAL_RECOVERY_SHA256" = \
	9170b0a8d58ca8a01dec0d800200fb2d22f6da5a8fda6b101e3fc753a6fd4bb1 ]

mkdir -p "$test_root/source/net/mac80211"
cat > "$test_root/source/net/mac80211/mlme.c" <<'EOF'

		for (i = 0; i < triplet->chans.num_channels; i++) {
			if (first_channel + i * chan_increment == chan) {
				have_chan_pwr = true;
				*chan_pwr = triplet->chans.max_power;
				break;
EOF
(
	cd "$test_root/source"
	patch --dry-run --fuzz=0 -p1 < "$patch_file" >/dev/null
	patch --fuzz=0 -p1 < "$patch_file" >/dev/null
)
grep -F 'triplet->chans.max_power == S8_MIN' \
	"$test_root/source/net/mac80211/mlme.c" >/dev/null
grep -F 'goto next;' "$test_root/source/net/mac80211/mlme.c" >/dev/null

bundle_dir="$test_root/bundle"
mkdir "$bundle_dir" "$test_root/inner"
printf 'placeholder\n' > "$test_root/inner/config"
tar -czf "$bundle_dir/dendelion-sysupgrade.tar.gz" \
	-C "$test_root/inner" config
cat > "$bundle_dir/system-board.json" <<'EOF'
{"board_name":"linksys,e8450-ubi","release":{"version":"25.12.5"}}
EOF
cat > "$bundle_dir/apk-installed-manifest.txt" <<'EOF'
mwan3 2.11.13-r1
sqm-scripts 1.6.0-r1
luci-app-mwan3 25.208.12345-r1
mwan3 2.11.13-r1
EOF
printf '/etc/config/network\n' > "$bundle_dir/sysupgrade-file-list.txt"
printf '2026-08-02T00:00:00Z\n' > "$bundle_dir/created-at-utc.txt"
(
	cd "$bundle_dir"
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum dendelion-sysupgrade.tar.gz system-board.json \
			apk-installed-manifest.txt sysupgrade-file-list.txt \
			created-at-utc.txt > SHA256SUMS
	else
		shasum -a 256 dendelion-sysupgrade.tar.gz system-board.json \
			apk-installed-manifest.txt sysupgrade-file-list.txt \
			created-at-utc.txt > SHA256SUMS
	fi
)
tar -czf "$test_root/dendelion-latest.tar.gz" -C "$bundle_dir" \
	dendelion-sysupgrade.tar.gz system-board.json \
	apk-installed-manifest.txt sysupgrade-file-list.txt created-at-utc.txt \
	SHA256SUMS

"$project_dir/packages-from-backup.sh" \
	"$test_root/dendelion-latest.tar.gz" "$test_root/packages.txt" >/dev/null
cat > "$test_root/expected-packages.txt" <<'EOF'
luci-app-mwan3
mwan3
sqm-scripts
EOF
cmp "$test_root/expected-packages.txt" "$test_root/packages.txt"

if "$project_dir/packages-from-backup.sh" \
	"$test_root/dendelion-latest.tar.gz" "$test_root/packages.txt" \
	>"$test_root/reuse.out" 2>&1; then
	printf 'expected package extractor to refuse an existing output\n' >&2
	exit 1
fi
grep -F 'output already exists' "$test_root/reuse.out" >/dev/null

if [ "${RUN_NETWORK_TESTS:-0}" = 1 ]; then
	artifacts="$test_root/artifacts"
	mkdir "$artifacts"
	patched_name="openwrt-$OPENWRT_RELEASE-$OPENWRT_TARGET-$OPENWRT_SUBTARGET-$OPENWRT_DEVICE-squashfs-sysupgrade-mac80211-s8min-fix.itb"
	printf 'synthetic image; never flash\n' > "$artifacts/$patched_name"
	if command -v sha256sum >/dev/null 2>&1; then
		image_hash=$(sha256sum "$artifacts/$patched_name" | awk '{print $1}')
		patch_hash=$(sha256sum "$patch_file" | awk '{print $1}')
	else
		image_hash=$(shasum -a 256 "$artifacts/$patched_name" | awk '{print $1}')
		patch_hash=$(shasum -a 256 "$patch_file" | awk '{print $1}')
	fi
	cat > "$artifacts/FWTOOL-METADATA.json" <<EOF
{"metadata_version":"1.1","compat_version":"$OPENWRT_COMPAT_VERSION","new_supported_devices":["$OPENWRT_BOARD"],"version":{"dist":"OpenWrt","version":"$OPENWRT_RELEASE","revision":"$OPENWRT_REVISION","target":"$OPENWRT_TARGET/$OPENWRT_SUBTARGET","board":"$OPENWRT_DEVICE"}}
EOF
	printf 'synthetic manifest\n' > "$artifacts/IMAGE-MANIFEST.txt"
	printf 'mwan3\tmwan3\n' > "$artifacts/PACKAGE-MAP.tsv"
	printf 'mwan3\n' > "$artifacts/PACKAGES-RESOLVED.txt"
	printf 'synthetic source sums\n' > "$artifacts/SOURCE-SHA256SUMS"
	printf 'CONFIG_TARGET_mediatek_mt7622=y\n' \
		> "$artifacts/config.buildinfo"
	cat > "$artifacts/feeds.buildinfo" <<'EOF'
src-git packages https://git.openwrt.org/feed/packages.git^5caa62e0bc9f7fb9b0c12a23267bceb7724214dd
src-git luci https://git.openwrt.org/project/luci.git^128a7812f4be233c5dd7f7466f534fd888785caf
src-git routing https://git.openwrt.org/feed/routing.git^3d7d0dc7fa43d3eb09498417407e95a6552e5312
src-git telephony https://git.openwrt.org/feed/telephony.git^2618106d5846a4a542fdf5809f0d3ed228ce439b
src-git video https://github.com/openwrt/video.git^094bf58da6682f895255a35a84349a79dab4bf95
EOF
	printf '{}\n' > "$artifacts/profiles.json"
	printf '%s\n' "$OPENWRT_REVISION" > "$artifacts/version.buildinfo"
	{
		printf 'OPENWRT_RELEASE=%s\n' "$OPENWRT_RELEASE"
		printf 'OPENWRT_SOURCE_COMMIT=%s\n' "$OPENWRT_SOURCE_COMMIT"
		printf 'OPENWRT_REVISION=%s\n' "$OPENWRT_REVISION"
		printf 'OPENWRT_TARGET=%s\n' "$OPENWRT_TARGET"
		printf 'OPENWRT_SUBTARGET=%s\n' "$OPENWRT_SUBTARGET"
		printf 'OPENWRT_DEVICE=%s\n' "$OPENWRT_DEVICE"
		printf 'OPENWRT_BOARD=%s\n' "$OPENWRT_BOARD"
		printf 'OPENWRT_COMPAT_VERSION=%s\n' "$OPENWRT_COMPAT_VERSION"
		printf 'OPENWRT_FEEDS_BUILDINFO_SHA256=%s\n' \
			"$OPENWRT_FEEDS_BUILDINFO_SHA256"
		printf 'PATCH_SHA256=%s\n' "$patch_hash"
		printf 'IMAGE=%s\n' "$patched_name"
		printf 'IMAGE_SHA256=%s\n' "$image_hash"
		printf 'IMAGE_SIGNED=no\n'
	} > "$artifacts/BUILD-INFO.txt"
	(
		cd "$artifacts"
		if command -v sha256sum >/dev/null 2>&1; then
			sha256sum BUILD-INFO.txt FWTOOL-METADATA.json \
				IMAGE-MANIFEST.txt PACKAGE-MAP.tsv PACKAGES-RESOLVED.txt \
				SOURCE-SHA256SUMS config.buildinfo feeds.buildinfo \
				profiles.json version.buildinfo "$patched_name" > SHA256SUMS
		else
			shasum -a 256 BUILD-INFO.txt FWTOOL-METADATA.json \
				IMAGE-MANIFEST.txt PACKAGE-MAP.tsv PACKAGES-RESOLVED.txt \
				SOURCE-SHA256SUMS config.buildinfo feeds.buildinfo \
				profiles.json version.buildinfo "$patched_name" > SHA256SUMS
		fi
	)
	"$project_dir/prepare-recovery-kit.sh" \
		--patched-image "$artifacts/$patched_name" \
		--backup "$test_root/dendelion-latest.tar.gz" \
		--output "$test_root/recovery-kit" >/dev/null
	(
		cd "$test_root/recovery-kit"
		if command -v sha256sum >/dev/null 2>&1; then
			sha256sum -c SHA256SUMS >/dev/null
		else
			shasum -a 256 -c SHA256SUMS >/dev/null
		fi
	)
	grep -Fx "OFFICIAL_SYSUPGRADE_SHA256=$OFFICIAL_SYSUPGRADE_SHA256" \
		"$test_root/recovery-kit/KIT-INFO.txt" >/dev/null
	grep -Fx "OFFICIAL_RECOVERY_SHA256=$OFFICIAL_RECOVERY_SHA256" \
		"$test_root/recovery-kit/KIT-INFO.txt" >/dev/null
	grep -Fx "OPENWRT_COMPAT_VERSION=$OPENWRT_COMPAT_VERSION" \
		"$test_root/recovery-kit/KIT-INFO.txt" >/dev/null

	duplicate_artifacts="$test_root/duplicate-artifacts"
	cp -R "$artifacts" "$duplicate_artifacts"
	printf 'OPENWRT_BOARD=conflicting-board\n' \
		>> "$duplicate_artifacts/BUILD-INFO.txt"
	(
		cd "$duplicate_artifacts"
		if command -v sha256sum >/dev/null 2>&1; then
			sha256sum BUILD-INFO.txt FWTOOL-METADATA.json \
				IMAGE-MANIFEST.txt PACKAGE-MAP.tsv PACKAGES-RESOLVED.txt \
				SOURCE-SHA256SUMS config.buildinfo feeds.buildinfo \
				profiles.json version.buildinfo "$patched_name" > SHA256SUMS
		else
			shasum -a 256 BUILD-INFO.txt FWTOOL-METADATA.json \
				IMAGE-MANIFEST.txt PACKAGE-MAP.tsv PACKAGES-RESOLVED.txt \
				SOURCE-SHA256SUMS config.buildinfo feeds.buildinfo \
				profiles.json version.buildinfo "$patched_name" > SHA256SUMS
		fi
	)
	if "$project_dir/prepare-recovery-kit.sh" \
		--patched-image "$duplicate_artifacts/$patched_name" \
		--backup "$test_root/dendelion-latest.tar.gz" \
		--output "$test_root/duplicate-recovery-kit" \
		> "$test_root/duplicate-build-info.out" 2>&1; then
		printf 'expected duplicate BUILD-INFO key to be rejected\n' >&2
		exit 1
	fi
	grep -F 'must contain OPENWRT_BOARD exactly once' \
		"$test_root/duplicate-build-info.out" >/dev/null
fi

printf 'kernel-5ghz-power: ok\n'
