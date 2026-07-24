# OpenWrt backup and restore

[Pi documentation index](../../README.md)

`openwrt_backup.sh` pulls a recovery bundle from `dendelion` before each Borg
archive. The router accepts only a dedicated SSH key whose `authorized_keys`
entry forces `/usr/libexec/openwrt-backup-export`, disables forwarding and PTY
allocation, and whose exporter verifies that the source address is vanpi.
That check currently expects vanpi's reserved address, `192.168.6.103`; changing
the reservation requires updating and redeploying the exporter.

The last verified bundle is stored at:

```text
/home/pi/backups/snapshots/openwrt/dendelion-latest.tar.gz
```

The file is replaced atomically only after its outer member list, SHA-256
checksums, router model, package manifest, nested sysupgrade archive, declared
file list, and required custom paths pass validation. If the pull fails, the
last valid bundle remains in place and the normal Borg backup continues with a
warning. `backup_watchdog.sh` alerts if no verified router snapshot exists or
if it is older than `OPENWRT_BACKUP_STALE_HOURS`.

OpenWrt reports backup paths with a leading `/`, while tar stores relative
paths. Validation normalizes that difference and permits only OpenWrt's generated
`etc/uci-defaults/10_disable_services` helper beyond the declared list.

The bundle contains:

- `dendelion-sysupgrade.tar.gz`: OpenWrt configuration and secrets.
- `system-board.json`: exact model, release, revision, and kernel metadata.
- `apk-installed-manifest.txt`: installed package names and versions.
- `sysupgrade-file-list.txt`: the paths OpenWrt declared for preservation.
- `created-at-utc.txt` and `SHA256SUMS`.

The router's `/etc/sysupgrade.conf` explicitly preserves the native TTL rule
and the backup exporter itself. Validation also requires the core LAN, DHCP,
firewall, wireless, MWAN, SQM, and SSH authorization files, and fails closed if
any of them drops out of the archive. The router refuses an export if the
restricted backup-key entry is no longer present in `authorized_keys`.

The bundle contains Wi-Fi credentials, password hashes, and private keys. It is
mode `0600`, its directory is mode `0700`, and the Borg repository is encrypted
with `repokey-blake2`. Root obtains the passphrase through `BORG_PASSCOMMAND`
from the root-only file configured in `backup_conf.sh`. Keep the passphrase and
exported Borg recovery key somewhere independent of both vanpi and `bigboi`;
neither a passphrase file nor key stored only inside this repository could
unlock it after a total Pi failure.

## Routine operation

The root cron invokes `pi_backup.sh` hourly from 03:00 through 08:00; the first
successful run each day wins. The OpenWrt pull occurs immediately before
`borg create`, so Borg's normal daily, weekly, and monthly retention versions
the router snapshot without a second retention system.

Manual snapshot test:

```bash
sudo /home/pi/scripts/backup/openwrt_backup.sh
sudo /home/pi/scripts/backup/backup_watchdog.sh
```

List the preserved paths without exposing file contents:

```bash
tar -xzOf /home/pi/backups/snapshots/openwrt/dendelion-latest.tar.gz \
  sysupgrade-file-list.txt
```

## Recover the bundle from Borg

Mount a selected Borg archive read-only, then copy the outer bundle somewhere
private. The path inside a mounted archive begins with `home`, not `/home`:

```bash
sudo -i
source /home/pi/scripts/backup/backup_conf.sh
repo=$BORG_REPO
mkdir -p /mnt/tmp
borg mount "$repo"::vanpi-YYYY-MM-DD_HHMM /mnt/tmp
cp /mnt/tmp/home/pi/backups/snapshots/openwrt/dendelion-latest.tar.gz /root/
borg umount /mnt/tmp
chmod 600 /root/dendelion-latest.tar.gz
```

Verify and extract the outer bundle:

```bash
sudo mkdir -p /root/dendelion-restore
sudo tar -xzf /root/dendelion-latest.tar.gz -C /root/dendelion-restore
cd /root/dendelion-restore
sudo sha256sum -c SHA256SUMS
sudo jq '{model,board_name,release}' system-board.json
```

## Restore OpenWrt

Do not blindly restore this archive onto a different router model or a different
major OpenWrt release. First read `system-board.json` and obtain the exact
official sysupgrade image named by its release. Keep the E8450 UBI migration and
boot-layout recovery kit separately; routine backups are not raw NAND images.

For an intact installation of the same release:

1. Connect over isolated Ethernet and confirm the target reports
   `linksys,e8450-ubi`.
2. Save any newer configuration before proceeding.
3. Compare `apk-installed-manifest.txt` with the target. Reinstall required
   packages such as `mwan3`, SQM, and their LuCI apps. Do not run a blanket
   `apk upgrade`.
4. Copy the nested archive to the router and restore it:

   ```bash
   scp dendelion-sysupgrade.tar.gz root@192.168.6.1:/tmp/
   ssh root@192.168.6.1 'sysupgrade -r /tmp/dendelion-sysupgrade.tar.gz'
   ssh root@192.168.6.1 reboot
   ```

5. Reconnect at `192.168.6.1` and verify LAN DHCP/DNS, both radios, firewall,
   TTL rewriting, `mwan3`, and SQM.

For a clean reflash, install the exact firmware first, reinstall the required
packages, then restore the nested archive. Across a major OpenWrt version,
selectively migrate UCI and custom files instead of wholesale restoration.

The backup does not contain installed package binaries or a bootable firmware
image. Keep the exact firmware image and its signed checksum with the separate
router recovery kit so recovery does not depend on internet availability.
