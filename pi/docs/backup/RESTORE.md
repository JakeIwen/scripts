# vanpi recovery playbook

[Pi documentation index](../../README.md)

Backups (see [`backup_conf.sh`](../../scripts/backup/backup_conf.sh)):
- **encrypted borg repo** `/mnt/bigboi/borg/vanpi-encrypted` — nightly versioned snapshots of `/` + `/boot/firmware`
  (14 daily / 8 weekly / 12 monthly). HA's sqlite is snapshotted to
  `/home/pi/backups/snapshots/` before each run; the live DB is excluded. A
  verified OpenWrt recovery bundle and a verified UBNT `/etc/persistent` export
  are pulled into the same snapshot tree before Borg runs; see
  [`OPENWRT_BACKUP.md`](../../scripts/backup/OPENWRT_BACKUP.md) and
  [`UBNT_BACKUP.md`](../../scripts/backup/UBNT_BACKUP.md). Unattended jobs read the
  passphrase from `/root/.config/borg/vanpi-encrypted.passphrase`. Keep an
  independent password-manager copy of that passphrase and an exported Borg key
  off-device; a copy stored only inside the encrypted archive is not recoverable.
- **hot-spare SD card(s)** — bootable clones via rpi-clone, staggered intervals per
  `CLONE_TARGETS`. Each card's `/boot/firmware/CLONE_INFO.txt` says when it was cloned.
- **EXFAT512 safety snapshots** — directly browsable trees under
  `/mnt/hdd1tb/backups/EXFAT512_YYYY-MM-DD_HH-MM/`. Unchanged files are hard-linked
  to the previous completed snapshot, while deleted or changed source files remain
  in retained older snapshots. Retention keeps daily snapshots for 30 days, one per
  week through 84 days, and one per month through 365 days.

OpenWrt's persistent remote log is `/var/log/openwrt/dendelion.log`; restore its
receiver with `sudo /home/pi/scripts/setup_openwrt_logging.sh`. See
[`OPENWRT_LOGGING.md`](../networking/OPENWRT_LOGGING.md) for configuration and
verification.

## Scenario 1 — SD card died, hot spare is attached

1. Power off. Move the spare card from the USB reader into the SD slot. Power on.
2. That's it — the clone carries its own PARTUUIDs in fstab/cmdline, so it just boots.
   You're now running at the state shown in its `CLONE_INFO.txt`.
3. The watchdog notices the / partition is still labeled `hotspare-*` and nags via ntfy
   (at boot and daily at 10:00) until you finish recovery:
   pull anything newer from borg (scenario 2), relabel the now-live card
   (`sudo e2label /dev/mmcblk0p2 rootfs`), then initialize a fresh spare:
   `sudo /home/pi/scripts/backup/clone_to_sd.sh --init hotspare-a sdX`

## Scenario 2 — restore individual files / roll back a mistake

```bash
# Run as root so backup_conf.sh can use the root-only passphrase file.
sudo -i
source /home/pi/scripts/backup/backup_conf.sh
repo=$BORG_REPO
mkdir -p /mnt/tmp
borg list $repo
borg mount $repo::vanpi-2026-07-01_1300 /mnt/tmp   # browse a snapshot read-only
borg umount /mnt/tmp
cd / && borg extract $repo::vanpi-2026-07-01_1300 home/pi/scripts/foo.sh   # restore in place
exit
```

Home Assistant DB: restore `home/pi/backups/snapshots/home-assistant_v2.db` from the
archive, stop `home-assistant.service`, copy it over
`/home/homeassistant/.homeassistant/home-assistant_v2.db` (remove `-wal`/`-shm`), start.

OpenWrt router: restore
`home/pi/backups/snapshots/openwrt/dendelion-latest.tar.gz` from a selected Borg
archive, then follow
[`OPENWRT_BACKUP.md`](../../scripts/backup/OPENWRT_BACKUP.md). The outer bundle
is not a firmware image and must not be flashed.

UBNT antenna: restore
`home/pi/backups/snapshots/ubnt/ubnt-persistent-latest.tar.gz` from a selected
Borg archive, then follow
[`UBNT_BACKUP.md`](../../scripts/backup/UBNT_BACKUP.md). Treat every extracted
profile as credential-bearing.

EXFAT512: mount `hdd1tb` through the normal guarded disk path, then copy the
needed file or directory from the selected snapshot. Do not modify a retained
snapshot in place because hard-linked unchanged files may also belong to other
snapshot directories:

```bash
sudo /home/pi/scripts/diskctl mount hdd1tb
ls -1 /mnt/hdd1tb/backups/
rsync -rt --info=progress2 \
  /mnt/hdd1tb/backups/EXFAT512_2026-07-31_03-00/path/to/data/ \
  /mnt/EXFAT512/path/to/data/
sudo /home/pi/scripts/diskctl eject hdd1tb
```

## Scenario 3 — SD card AND spare both dead

1. On the MacBook: flash **Raspberry Pi OS 64-bit** (any recent) to a card with rpi-imager.
2. Retrieve the Borg passphrase from the password manager. If Borg cannot read
   the repository's embedded key, also retrieve the exported key. Boot the Pi
   from the new card, attach bigboi, then:
   ```bash
   sudo apt install borgbackup
   sudo mount /dev/disk/by-label/bigboi /mnt/bigboi
   sudo install -d -m 0700 /root/.config/borg
   sudo install -m 0600 /dev/null /root/.config/borg/vanpi-encrypted.passphrase
   sudoedit /root/.config/borg/vanpi-encrypted.passphrase
   sudo -i
   export BORG_REPO=/mnt/bigboi/borg/vanpi-encrypted
   export BORG_PASSCOMMAND='/usr/bin/cat /root/.config/borg/vanpi-encrypted.passphrase'
   borg extract --numeric-ids ::$(borg list --last 1 --format '{archive}') home/pi/scripts
   exit
   # then use the restored script for the full job, onto a second card in a USB reader:
   sudo home/pi/scripts/restore_from_borg.sh sdX
   ```
   If the repository's embedded key is unavailable, import the independent key
   copy with `borg key import /path/to/exported-key` after setting the two Borg
   environment variables. Or run `restore_from_borg.sh` directly if any
   surviving system still has the scripts and passphrase file.
3. Swap the restored card into the SD slot and boot.

## Van-ignition interplay

Backups run hourly 03:00–08:00 (when the van is least likely to drive); the first
success of the day wins. Scheduled attempts first read requested state through
`policyctl` and defer without mounting anything while HDD policy is disabled.
If policy cannot be verified, the job fails closed and the backup watchdog
eventually reports the stale backup. For a deliberate parked manual run, enable
disks through `policyctl` or the dashboard first, then use
`sudo /home/pi/scripts/backup/backup_window.sh --force` to run both daily
backup jobs and bypass only their once-per-day shortcuts. Requested HDD policy
and ignition remain authoritative.

While the van runs
(`~/hooks/ignition_is_on` exists) attempts defer, since drives are unmounted for
vibration protection. Starting the van mid-backup is safe: `umount_disks.sh` calls
`backup/abort_backup.sh`, which TERMs the running job and waits for it to stop before
unmounting — borg simply rolls back to its last checkpoint and the next parked hour
retries. A restore aborted this way leaves the target card INCOMPLETE (you'll get a
loud ntfy) — just re-run it when parked.

For normal completion, `pi_backup.sh` unmounts `bigboi` only when that backup
run mounted it. If `bigboi` was already mounted, the backup leaves it mounted.
It delegates mounts to `mount_disks.sh`, so backup runs use the same exact-label,
stale-source, underlay, and root-disk safeguards as policy reconciliation.
Ignition handling and an explicit dashboard/user eject remain authoritative
lifecycle operations and intentionally unmount it regardless of who mounted it.
`exfat_snapshot.sh` always unmounts and spins down `hdd1tb` after its work,
including cleanup after a failure. A hidden `.EXFAT512.partial` directory is
resumed on retry and is never treated as a completed restore point.

## Watchdog

`backup_watchdog.sh` (daily cron) sends ntfy alerts when: no successful Borg or
EXFAT512 snapshot exists within 48h, the `hdd1tb` target is absent, no verified
OpenWrt or UBNT snapshot exists or either is stale, a clone exceeds 2× its
interval, a card was never cloned, bigboi is unmounted, or free space < 100GB.
Silence = healthy, but the nightly "vanpi backup OK" ping (min priority) includes
per-card clone age if you want positive confirmation.
