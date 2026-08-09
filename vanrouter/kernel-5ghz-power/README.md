# Dendelion 5 GHz transmit-power patch

This directory contains a narrow OpenWrt 25.12.5 patch and a recovery-gated
build/deployment workflow for the Linksys E8450 UBI installation. None of the
scripts deploys anything merely by being built or tested.

## What the patch changes

When `radio1` associates as a 5 GHz station, mac80211 reads the upstream
Country element. The observed peer value is `0x80`; the Country triplet field
is signed, so mac80211 reads it as `-128` dBm. The existing 802.11h calculation
then clamps the result to 0 dBm. Since the E8450's station and `dendelion` AP
share one PHY, both interfaces inherit the unusable power ceiling.

`patches/999-mac80211-ignore-s8-min-country-power.patch` rejects only
`S8_MIN` (`-128`) when it is the matching Country-triplet power. It does not
raise the regulatory limit, override a configured power limit, ignore valid
negative values, or disable ordinary 802.11h constraints. If no other valid
triplet matches, mac80211 falls back to the locally enforced regulatory
maximum.

This is a generic mac80211 interoperability workaround, not a MediaTek power
boost. The mt76/MediaTek topology explains why the bad station constraint also
damages the AP on this router.

The patch is pinned to:

- OpenWrt `v25.12.5`, commit `f0a60eee2fe051741c643ea6118718aae1ef17fb`
- OpenWrt revision `r33051-f5dae5ece4`
- mac80211 backports `6.18.26`
- board `linksys,e8450-ubi`

Finalization also requires `feeds.buildinfo` to match the exact package, LuCI,
routing, telephony, and video feed revisions published for this release. A
later moving feed checkout cannot silently produce an artifact under the same
build identity.

The build also requires complete Git history. OpenWrt derives the
`base-files` package release from the number of commits that touched its source
directory. For this release that count is `1711`, producing
`base-files - 1711~f5dae5ece4`. A depth-one clone instead produces the invalid
version `1~f5dae5ece4`; the builder, finalizer, and recovery-kit builder all
reject that condition.

`release.conf` also pins the official rollback and recovery image hashes from
the OpenWrt 25.12.5 mediatek/mt7622 release.

## Validate the source and patch on this Mac

Use an exact-tag, full-history checkout. This check downloads the hash-pinned
backports archive, dry-runs the complete existing OpenWrt subsystem patch
series, then dry-runs this patch with zero fuzz, applies it, and confirms the
changed expression:

```bash
cd /Users/jacobr/dev/scripts
git clone --branch v25.12.5 --single-branch \
  https://git.openwrt.org/openwrt/openwrt.git \
  /private/tmp/openwrt-25.12.5
./vanrouter/kernel-5ghz-power/build-openwrt.sh \
  --check-source /private/tmp/openwrt-25.12.5
```

## Build the sysupgrade image on Linux

OpenWrt's full build is Linux-only. Start with a clean exact-tag source tree
and a fresh verified router backup. The package seed includes the current
installed set, so the new firmware does not silently omit MWAN, SQM, LuCI, or
their dependencies.

```bash
cd /path/to/scripts
mkdir -p vanrouter/build
./vanrouter/kernel-5ghz-power/packages-from-backup.sh \
  /path/to/dendelion-latest.tar.gz \
  vanrouter/build/dendelion-packages-25.12.5.txt
git clone --branch v25.12.5 --single-branch \
  https://git.openwrt.org/openwrt/openwrt.git \
  /path/to/openwrt-25.12.5
./vanrouter/kernel-5ghz-power/build-openwrt.sh \
  --source /path/to/openwrt-25.12.5 \
  --packages vanrouter/build/dendelion-packages-25.12.5.txt \
  --output vanrouter/build/openwrt-25.12.5-mac80211-s8min \
  --jobs 4
```

The immutable input checkout is cloned into the output directory before the
patch or configuration is added. Installed APK names that carry an ABI suffix,
such as `jansson4`, are resolved against the pinned source metadata to their
build selectors, such as `jansson`; unresolved or ambiguous names stop the
build. The target-provided `kernel` package is handled explicitly. A successful
build emits the custom `.itb`, its SHA-256, `BUILD-INFO.txt`, and the resolved
package map under `artifacts/`. The whole `vanrouter/build` directory is ignored
by Git.

Both the input checkout and the cloned build source must report `false` for
`git rev-parse --is-shallow-repository` and `1711` for this command:

```bash
git rev-list --count f0a60eee2fe051741c643ea6118718aae1ef17fb -- \
  package/base-files
```

Unshallowing a completed build tree cannot repair an image already stamped
with the wrong package version. Rebuild into a new output directory.

On vanpi, launch the long build as a system-scoped transient service so an SSH
disconnect cannot terminate it. This returns immediately while systemd runs
the build as `pi` and appends output to a file:

```bash
ssh pi@192.168.6.103 'sudo systemd-run \
  --unit=dendelion-openwrt-build-fullhistory \
  --uid=pi --gid=pi --working-directory=/home/pi/build \
  --property=UMask=0022 --property=Nice=10 \
  --property=IOSchedulingClass=best-effort \
  --property=IOSchedulingPriority=7 \
  --property=StandardOutput=append:/home/pi/build/openwrt-25.12.5-mac80211-s8min-fullhistory.log \
  --property=StandardError=append:/home/pi/build/openwrt-25.12.5-mac80211-s8min-fullhistory.log \
  /home/pi/build/kernel-5ghz-power-toolkit/build-openwrt.sh \
  --source /home/pi/build/openwrt-25.12.5-source \
  --packages /home/pi/build/dendelion-packages-25.12.5.txt \
  --output /home/pi/build/openwrt-25.12.5-mac80211-s8min-fullhistory \
  --jobs 1'
```

The service survives SSH logout but not a Pi reboot. Inspect it without
attaching to the build process:

```bash
ssh pi@192.168.6.103 'systemctl show \
  dendelion-openwrt-build-fullhistory.service \
  -p ActiveState -p SubState -p Result -p ExecMainStatus; \
  tail -n 50 \
  /home/pi/build/openwrt-25.12.5-mac80211-s8min-fullhistory.log; \
  df -h /home/pi'
```

The finalizer reads the actual sysupgrade filename, size, and SHA-256 from
OpenWrt's `profiles.json`; it does not assume that `CONFIG_VERSION_FILENAMES`
was enabled. If compilation completed but finalization did not, rerun only the
non-building finalizer:

```bash
./vanrouter/kernel-5ghz-power/finalize-openwrt-build.sh \
  --output vanrouter/build/openwrt-25.12.5-mac80211-s8min
```

It refuses an existing `artifacts/` directory and validates the source commit,
complete history, sole patch, target metadata, embedded sysupgrade metadata,
image checksums, exact `base-files` version, and exact requested package set
before atomically creating artifacts.

## Build the private recovery kit

Run this only after the patched image exists. The kit builder verifies the
backup's internal checksums and exact board/release, verifies `BUILD-INFO.txt`
against this patch, and downloads both official OpenWrt images. It checks the
normal UBI sysupgrade image and initramfs recovery image against the pinned
official SHA-256 values.

```bash
cd /path/to/scripts
./vanrouter/kernel-5ghz-power/prepare-recovery-kit.sh \
  --patched-image \
  vanrouter/build/openwrt-25.12.5-mac80211-s8min/artifacts/openwrt-25.12.5-mediatek-mt7622-linksys_e8450-ubi-squashfs-sysupgrade-mac80211-s8min-fix.itb \
  --backup /path/to/dendelion-latest.tar.gz \
  --output vanrouter/build/dendelion-recovery-kit
```

The kit contains secrets because it includes the router backup. Keep its
directory mode `0700`, do not commit it, and copy it to vanpi only over the
trusted LAN.

## Future deployment from vanpi

Do not run these commands until a deployment day. Copy the complete private kit
to vanpi, verify that vanpi's `eth0` is physically connected to a LAN port, and
run preflight as the normal `pi` user. Preflight refuses a non-`eth0` route,
runs the existing verified backup exporter, checks live board/release metadata,
copies the image only to router RAM, verifies its hash there, and runs
`sysupgrade -T`. It then deletes the staged image without flashing.

```bash
cd /home/pi/dendelion-recovery-kit
./image-action-from-vanpi.sh preflight patched . root@192.168.6.1
```

The command prints the full confirmation hash. Only the `execute` form can
flash, and it must receive that exact full hash:

```bash
cd /home/pi/dendelion-recovery-kit
./image-action-from-vanpi.sh execute patched . \
  PASTE_THE_FULL_PREFLIGHT_SHA256_HERE root@192.168.6.1
```

The normal sysupgrade preserves configuration. It does not write the E8450
preloader, U-Boot, or the installer-created recovery volume.

After five minutes, verify production boot, the wired LAN, both `radio1`
interfaces, and the absence of a 0 dBm ceiling:

```bash
ssh root@192.168.6.1 '/bin/ubus call system board; \
  /usr/sbin/iw dev wl1-sta0 info; /usr/sbin/iw dev wl1-ap0 info; \
  /sbin/logread | /bin/grep -F "Limiting TX power" | /usr/bin/tail -20'
```

The expected result is OpenWrt 25.12.5 on `linksys,e8450-ubi`, with both
interfaces present and no current `Limiting TX power to 0 (-128 - 0)` event.
Then verify WAN routing, TTL rewriting, MWAN, SQM, DHCP/DNS, and a real 5 GHz
client before considering the canary successful.

## Rollback levels

### 1. Router still reachable

Use the exact official 25.12.5 UBI image already in the kit. Preflight and
execute have the same wired-route, fresh-backup, board, checksum, and
`sysupgrade -T` gates:

```bash
cd /home/pi/dendelion-recovery-kit
./image-action-from-vanpi.sh preflight rollback . root@192.168.6.1
./image-action-from-vanpi.sh execute rollback . \
  189ef531f2e9a43b5ce2e988c4195b16f7a8eb5707bf45304a3ebcbb2bfaf9ed \
  root@192.168.6.1
```

The official image may omit packages added after the stock profile. The fresh
backup retains the package manifest and configuration; reinstall the required
packages before restoring service-specific configuration if necessary. Do not
run a blanket `apk upgrade`.

### 2. Production OpenWrt is unreachable but recovery still boots

The UBI installer placed a separate recovery system on the router. Power the
router off, hold RESET while powering it on, and release RESET when the power
LED becomes orange/yellow. Connect vanpi by Ethernet and place its port on the
recovery subnet:

```bash
sudo ip addr flush dev eth0
sudo ip addr add 192.168.1.2/24 dev eth0
ping -c 3 192.168.1.1
scp openwrt-25.12.5-mediatek-mt7622-linksys_e8450-ubi-squashfs-sysupgrade.itb \
  root@192.168.1.1:/tmp/rollback.itb
ssh root@192.168.1.1 'sha256sum /tmp/rollback.itb'
ssh root@192.168.1.1 'sysupgrade -T /tmp/rollback.itb'
# Run only after manually matching the checksum printed above:
ssh root@192.168.1.1 'sysupgrade -n /tmp/rollback.itb'
```

Confirm the printed SHA-256 is
`189ef531f2e9a43b5ce2e988c4195b16f7a8eb5707bf45304a3ebcbb2bfaf9ed`
before permitting the final command. `-n` deliberately restores a clean base;
restore the verified configuration bundle only after required packages are
present.

### 3. Recovery does not boot

A normal sysupgrade does not touch the boot chain, so this would be a separate
or pre-existing failure. Stop rather than rerunning the UBI installer. Attach a
wired 3.3 V UART with ground and crossed TX/RX, leaving VCC disconnected, then
open the console at 115200 8N1:

```bash
ls -1 /dev/cu.*
screen /dev/cu.SLAB_USBtoUART 115200
```

The HM-10 Bluetooth module is not a dependable boot console. A CP2102 on a
NodeMCU board is potentially usable only with the ESP8266 held in reset and
after a bench loopback test. It does not have to remain connected, but testing
the wired adapter and locating the header before deployment reduces recovery
time. Use the boot menu/recovery image in the kit; use `mtk_uartboot` or the
separate UBI installer recovery procedure only if the bootloader itself is
actually unavailable.

## Tests

```bash
./vanrouter/tests/test_kernel_5ghz_power.sh
```

The test suite is non-deploying. It checks shell syntax, pinned identifiers,
zero-fuzz patch application to the expected source context, and package-list
extraction from a synthetic verified backup. Set `RUN_NETWORK_TESTS=1` to also
download, checksum, and assemble the official recovery files into a synthetic
private kit.
