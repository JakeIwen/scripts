# vanpi recovery playbook

Backups (see `scripts/backup_conf.sh`):
- **borg repo** `/mnt/bigboi/borg/vanpi` — nightly versioned snapshots of `/` + `/boot/firmware`
  (14 daily / 8 weekly / 12 monthly). HA's sqlite is snapshotted to
  `/home/pi/backups/snapshots/` before each run; the live DB is excluded.
- **hot-spare SD card(s)** — bootable clones via rpi-clone, staggered intervals per
  `CLONE_TARGETS`. Each card's `/boot/firmware/CLONE_INFO.txt` says when it was cloned.

## Scenario 1 — SD card died, hot spare is attached

1. Power off. Move the spare card from the USB reader into the SD slot. Power on.
2. That's it — the clone carries its own PARTUUIDs in fstab/cmdline, so it just boots.
   You're now running at the state shown in its `CLONE_INFO.txt`.
3. Pull anything newer from borg (scenario 2), then initialize a fresh spare:
   `sudo clone_to_sd.sh --init hotspare-a sdX`

## Scenario 2 — restore individual files / roll back a mistake

```bash
borg list                                  # see archives (BORG_REPO is exported via backup_conf.sh)
borg mount ::vanpi-2026-07-01_1300 /mnt/tmp   # browse a snapshot read-only
borg umount /mnt/tmp
cd / && borg extract ::vanpi-2026-07-01_1300 home/pi/scripts/foo.sh   # restore in place
```

Home Assistant DB: restore `home/pi/backups/snapshots/home-assistant_v2.db` from the
archive, stop `home-assistant.service`, copy it over
`/home/homeassistant/.homeassistant/home-assistant_v2.db` (remove `-wal`/`-shm`), start.

## Scenario 3 — SD card AND spare both dead

1. On the MacBook: flash **Raspberry Pi OS 64-bit** (any recent) to a card with rpi-imager.
2. Boot the Pi from it, attach bigboi, then:
   ```bash
   sudo apt install borgbackup
   sudo mount /dev/disk/by-label/bigboi /mnt/bigboi
   export BORG_REPO=/mnt/bigboi/borg/vanpi
   sudo --preserve-env=BORG_REPO borg extract --numeric-ids ::$(borg list --last 1 --format '{archive}') home/pi/scripts
   # then use the restored script for the full job, onto a second card in a USB reader:
   sudo home/pi/scripts/restore_from_borg.sh sdX
   ```
   (Or run `restore_from_borg.sh` directly if any surviving system still has the scripts.)
3. Swap the restored card into the SD slot and boot.

## Watchdog

`backup_watchdog.sh` (daily cron) sends ntfy alerts when: no successful borg backup in
48h, a clone exceeds 2× its interval, a card was never cloned, bigboi is unmounted, or
free space < 100GB. Silence = healthy, but the nightly "vanpi backup OK" ping (min
priority) includes per-card clone age if you want positive confirmation.
