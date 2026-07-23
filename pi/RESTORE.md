# vanpi recovery playbook

Backups (see `scripts/backup/backup_conf.sh`):
- **encrypted borg repo** `/mnt/bigboi/borg/vanpi-encrypted` — nightly versioned snapshots of `/` + `/boot/firmware`
  (14 daily / 8 weekly / 12 monthly). HA's sqlite is snapshotted to
  `/home/pi/backups/snapshots/` before each run; the live DB is excluded. A
  verified OpenWrt recovery bundle is pulled into the same snapshot tree before
  Borg runs; see `scripts/backup/OPENWRT_BACKUP.md`. Unattended jobs read the
  passphrase from `/root/.config/borg/vanpi-encrypted.passphrase`. Keep an
  independent password-manager copy of that passphrase and an exported Borg key
  off-device; a copy stored only inside the encrypted archive is not recoverable.
- **hot-spare SD card(s)** — bootable clones via rpi-clone, staggered intervals per
  `CLONE_TARGETS`. Each card's `/boot/firmware/CLONE_INFO.txt` says when it was cloned.

OpenWrt's persistent remote log is `/var/log/openwrt/dendelion.log`; restore its
receiver with `sudo /home/pi/scripts/setup_openwrt_logging.sh`. See
`OPENWRT_LOGGING.md` for configuration and verification.

## Scenario 1 — SD card died, hot spare is attached

1. Power off. Move the spare card from the USB reader into the SD slot. Power on.
2. That's it — the clone carries its own PARTUUIDs in fstab/cmdline, so it just boots.
   You're now running at the state shown in its `CLONE_INFO.txt`.
3. The watchdog notices the / partition is still labeled `hotspare-*` and nags via ntfy
   (at boot and daily at 10:00) until you finish recovery:
   pull anything newer from borg (scenario 2), relabel the now-live card
   (`sudo e2label /dev/mmcblk0p2 rootfs`), then initialize a fresh spare:
   `sudo clone_to_sd.sh --init hotspare-a sdX`

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
archive, then follow `scripts/backup/OPENWRT_BACKUP.md`. The outer bundle is not
a firmware image and must not be flashed.

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
success of the day wins. While the van runs
(`~/hooks/ignition_is_on` exists) attempts defer, since drives are unmounted for
vibration protection. Starting the van mid-backup is safe: `umount_disks.sh` calls
`backup/abort_backup.sh`, which TERMs the running job and waits for it to stop before
unmounting — borg simply rolls back to its last checkpoint and the next parked hour
retries. A restore aborted this way leaves the target card INCOMPLETE (you'll get a
loud ntfy) — just re-run it when parked.

## Watchdog

`backup_watchdog.sh` (daily cron) sends ntfy alerts when: no successful Borg backup in
48h, no verified OpenWrt snapshot exists or it is stale, a clone exceeds 2× its
interval, a card was never cloned, bigboi is unmounted, or free space < 100GB.
Silence = healthy, but the nightly "vanpi backup OK" ping (min priority) includes
per-card clone age if you want positive confirmation.
