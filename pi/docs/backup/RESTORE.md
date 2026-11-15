# vanpi recovery playbook

[Pi documentation index](../../README.md)

Backups (see [`backup_conf.sh`](../../scripts/backup/backup_conf.sh)):
- **weekly offsite iCloud copy** — encrypted Borg repository generations under
  `VanRecovery/vanpi/weekly`, with a completion marker written only after
  download verification. Requires initial Apple login and periodic renewal;
  see [iCloud setup and recovery](ICLOUD.md). Local installation alone is not
  evidence that a remote recovery generation has completed.
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
- **hot-spare SD card(s)** — bootable clones via rpi-clone, alternating one card per
  `CLONE_ROTATION_DAYS` (default 7 days). Each card normally refreshes every 14 days. Each card's `/boot/firmware/CLONE_INFO.txt` says when it was cloned.
- **EXFAT512 safety snapshots** — directly browsable trees under
  `/mnt/hdd1tb/backups/EXFAT512_YYYY-MM-DD_HH-MM/`. Unchanged files are hard-linked
  to the previous completed snapshot, while deleted or changed source files remain
  in retained older snapshots. Retention keeps daily snapshots for 30 days, one per
  week through 84 days, and one per month through 365 days. The `van-tv/` directory is intentionally excluded from these snapshots.

OpenWrt's persistent remote log is `/var/log/openwrt/dendelion.log`; restore its
receiver with `sudo /home/pi/scripts/setup_openwrt_logging.sh`. See
[`OPENWRT_LOGGING.md`](../networking/OPENWRT_LOGGING.md) for configuration and
verification.

MovingParts and BigBoi use Samba `fruit streams_xattr` for Mac metadata.
Resource forks remain in `._` AppleDouble files (`fruit:resource = file`);
Finder metadata uses Netatalk-compatible xattrs. The media mirror uses
`rsync -aHX` and preserves AppleDouble files. Retain both xattrs and sidecars
when restoring media, including Finder custom icons. These ordinary shares
do not advertise Time Machine support. Existing SMB clients must reconnect
after changing the module configuration; Apple's AAPL extensions are negotiated
on the first share connection, so connecting first to a non-fruit share can
still disable those extensions for that connection.

### Hotspare rotation

The dashboard's **Backup settings → Hotspares → Rotation interval** controls the
minimum gap between successful automatic refreshes. The `i` tooltip explains
missed-run and missing-card behavior. Changes save on blur or Enter.

Rotation selects the older card and preserves its peer. After missed runs, only
one card refreshes; the next waits a full interval from that success. Both cards
must be safely identified and unmounted. A missing/ambiguous card holds rotation
instead of causing the remaining card to be overwritten. A returning card does
not trigger two back-to-back copies. Existing label and capacity checks still
apply; no automatic initializer is run.

An interrupted or failed write leaves `clone_<label>.incomplete` beside the
success stamps. The scheduler retries that card, retaining the other generation;
it does not trust an older success stamp as proof that a partial card is intact.
With no recorded completed peer, automatic rotation stops for manual recovery.
The dashboard reports the hold/incomplete state and the watchdog retains its
freshness warnings. **Clone now** is an explicit manual override of rotation
spacing and peer availability, while keeping its existing target safety checks.

The normal per-card cadence and stale-warning basis are twice the rotation
interval. Older saved A/B settings migrate to the A interval (or half B when only
B was overridden), with other settings retained. No card or success timestamp is
changed by this migration.

## Scenario 1 — SD card died, hot spare is attached

1. Power off. Move the spare card from the USB reader into the SD slot. Power on.
2. That's it — the clone carries its own PARTUUIDs in fstab/cmdline, so it just boots.
   You're now running at the state shown in its `CLONE_INFO.txt`.
3. The watchdog notices the / partition is still labeled `hotspare-*` and nags via ntfy
   (at boot and daily at 10:00) until you finish recovery:
   pull anything newer from borg (scenario 2), relabel the now-live card
   (`sudo e2label /dev/mmcblk0p2 rootfs`), then replace exactly one missing spare
   while leaving the other configured spare attached. Verify the automatic detection,
   then initialize the new card with a full clone:
   ```bash
   sudo /home/pi/scripts/backup/new_hotspare.sh --dry-run
   sudo /home/pi/scripts/backup/new_hotspare.sh
   ```
   The script infers the replacement generation from the one missing `CLONE_TARGETS`
   label, accepts only one unmounted similarly sized card in an explicitly approved
   USB-reader model, shows the device it will erase, and checks the mapping again
   immediately before calling `clone_to_sd.sh --init`.

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

Backup jobs own resources explicitly (see [CONCURRENCY.md](CONCURRENCY.md)):
each job holds an exclusive per-kind lock with its PID record, a shared lock per
disk it uses, an exclusive `data-borg-repo` lock while the Borg repository is
mutated or copied, and the two cloud workers share one exclusive upload slot. The
registry lives in `/run/lock/vanpi-backup/` and is pre-created at boot by the
`vanpi-backup.conf` systemd-tmpfiles rule as `root:pi 0660` files in a
`root:pi 0770` directory. `/run/lock/vanpi_backup.lock` remains the legacy
whole-system lock: migrated jobs hold it shared, while `restore_from_borg.sh`,
`new_hotspare.sh` and the deployment fences hold it exclusively, so a restore
still excludes every job and any running job still refuses a restore. Lock
acquisition fails closed if a lock file or the directory is missing, symlinked,
or has different ownership or permissions, and never creates lock files. Status
checks must open the existing locks read-only; do not use path-form
`flock /run/lock/... ...` as a probe because path-form flock may create a
missing file under the caller's ownership.

Verify or reconstruct the runtime lock registry with:

```bash
sudo systemd-tmpfiles --create /etc/tmpfiles.d/vanpi-backup.conf
stat -c '%A %U:%G %n' /run/lock/vanpi_backup.lock /run/lock/vanpi-backup /run/lock/vanpi-backup/*.lock
# Expected: -rw-rw---- root:pi for every lock file, drwxrwx--- root:pi for the directory
```

Borg and the EXFAT snapshot run concurrently from `backup_window.sh`; the Pi and
Mac cloud uploads may overlap both, but never each other. `umount_disks.sh`
passes the labels it is about to unmount to `abort_backup.sh`, which
stops only the jobs holding those disks and waits for each disk lock to drain,
then claims those disks exclusively until the unmount and spindown finish;
ignition's emergency mode kills remaining holders of those lock files only.

The dashboard can request a graceful user stop only for the exact running
`pi_backup.sh` or `exfat_snapshot.sh` recorded in that kind's job lock. It sends
no emergency kill, never touches the other kind or the cloud workers, and
refuses restores, hotspare initialization, and unrelated lock holders. A
stopped Borg run is not stamped successful; an interrupted hotspare remains
unstamped. A stopped EXFAT run retains its hidden partial snapshot for retry and
performs its normal `hdd1tb` unmount/spindown cleanup before reporting stopped.

For normal completion, `pi_backup.sh` unmounts `bigboi` only when that backup
run mounted it and no other job (such as the Pi cloud upload) still holds the
disk. If `bigboi` was already mounted or is still in use, the backup leaves it
mounted.
It delegates mounts to `mount_disks.sh`, so backup runs use the same exact-label,
stale-source, underlay, and root-disk safeguards as policy reconciliation.
Ignition handling and an explicit dashboard/user eject remain authoritative
lifecycle operations and intentionally unmount it regardless of who mounted it.
`exfat_snapshot.sh` always unmounts and spins down `hdd1tb` after its work,
including cleanup after a failure. A hidden `.EXFAT512.partial` directory is
resumed on retry and is never treated as a completed restore point.

## Watchdog

`backup_watchdog.sh` (daily root cron) sends ntfy alerts when: no successful Borg
or EXFAT512 snapshot exists within 48h, the `hdd1tb` target is absent, no
verified OpenWrt or UBNT snapshot exists or either is stale, a clone exceeds 2×
its interval, a card was never cloned, bigboi is unmounted, or free space <
100GB. It also warns before root usage outgrows the bootable clone cards.
Configure their nominal capacity from the Backups pane using the
32/64/128/256GB selector. The dashboard stores that validated selection in
`/home/pi/backups/clone_card_nominal_gb`; `CLONE_CARD_NOMINAL_GB` in
`backup_conf.sh` remains the default when no selection has been saved. The
default 64GB setting produces a conservative 52GiB root-usage limit.

`rootfs_budget.sh`, sourced by the watchdog, records one root-used sample per
UTC day in `/home/pi/backups/stamps/rootfs_used_mib.history`. Replacement is
atomic and the history retains at most 120 samples. The default growth warning
is a seven-day rate above 4GiB (`ROOT_GROWTH_WARN_GIB_7D`), calculated from the
newest sample at least seven days old and normalized when a daily run was
missed. Each configured path is measured with a 300-second timeout, idle I/O
priority, low CPU priority, and same-filesystem `du -x`. A timeout, failed
measurement, absolute root limit, growth warning, or over-budget path is a
watchdog problem. Every rootfs alert includes all successfully measured path
sizes, largest first, plus explicit measurement failures.

Edit the single `ROOTFS_PATH_BUDGETS_MIB` array in `backup_conf.sh`; values are
MiB so fractional-GiB limits are exact:

| Path | Budget |
| --- | ---: |
| `/home/pi/dev/obd-things/tmp/compute` | 2.5GiB |
| `/home/pi/dev/obd-things/tmp` (whole) | 5GiB |
| `/home/pi/.codex` | 3GiB |
| `/var/lib/van-telemetry` | 2.5GiB |
| `/var/lib/vanpi-monitor` | 1.2GiB |
| `/var/log` | 1GiB |
| `/var/cache/apt` | 1GiB |
| `/home/pi/youtube-cast/releases` | 0.5GiB |
| `/home/pi/Documents/agent-shared` | 2GiB |

Each run also appends the measured sizes to
`/home/pi/backups/stamps/rootfs_paths.history` (`<utc-day> <epoch>
<path>:<MiB> ...`, one line per day, lines older than 400 days dropped).

Level and forecast alerts (`rootfs_check_alerts`) are separate from the daily
problem nag. The percent is `df`'s: used / (used + avail), rounded up. Crossing
70, 80 or 90% sends one ntfy message (on the `NTFY_WARNING_URL` topic through the
watchdog's notify; one message for the highest level when several are crossed at
once) and is recorded in `/home/pi/backups/stamps/rootfs_alert.state`. A level
alerts again only after usage falls to 5 points below it (70 re-arms at 65%; `ROOTFS_ALERT_HYSTERESIS`).
The percent comes from the raw KiB counters (a negative `avail`, when root uses reserved blocks, counts as full). The read-send-write of the state runs under a non-blocking `flock` on `rootfs_alert.state.lock` beside it; an overlapping run skips its alerts and logs that it did.
The state is written only after a successful send, so a failed send retries on the
next run. The forecast fits a least-squares line to the last 30 days of
`rootfs_used_mib.history` (at least 7 points) and alerts once, with the projected
date, when 90% is under 30 days away; it re-arms when the projection is 35 or more
days out or the trend is flat. A reading already at 90% is covered by the level
alert. `ROOTFS_TEST_PERCENT=91 backup_watchdog.sh`-style runs force the percent
(label "forced by ROOTFS_TEST_PERCENT") to test delivery; the state file then
shows the level as alerted and the next run is silent. The settings above live in
`backup_conf.sh`.

The watchdog log carries each problem's text on `  problem:` lines after its
summary line. The context line (`rootfs paths, largest first: ...`) counts as one
of the problems in the `problems: N` total.

The absolute limit (`ROOT_USED_MAX_GB`, 13/16 of the nominal clone-card size, 52GiB
for 64GB cards) keeps root usage inside what a bootable clone card holds, and the
4GiB/7-day growth limit warns of a rate that would breach it within weeks; the
percentage alerts are in addition to both, not a replacement. The system monitor's
`root_disk_full` event (used/total, about 4 points lower than `df`) is
record-only and never notifies; this watchdog is the notifying check.

The tracked watchdog entries are in root's crontab. Running as root lets the
bounded `du` commands traverse both root-owned cache/log paths and Pi-owned
state with restrictive modes; moving the watchdog to the `pi` crontab would
turn unreadable paths into explicit measurement-failure alerts.

Silence = healthy, but the nightly "vanpi backup OK" ping (min priority) includes
per-card clone age if you want positive confirmation.

## Root filesystem tidy

`/home/pi/scripts/rootfs_tidy.sh` is dry-run by default. Codex release retention
is delegated to `cleanup_codex_releases.py` (from `shared/sh/`, copied beside it by
the broad sync), the same script the MacBook runs weekly. Only a root run cleans
Codex releases: it calls the script with `--owner pi --package all`, which checks
the standalone and app-server-daemon package trees independently, performs every
file operation with `pi`'s effective IDs and resumes root only to read `/proc`:

```bash
/home/pi/scripts/rootfs_tidy.sh
sudo /home/pi/scripts/rootfs_tidy.sh --apply
```

In each package tree, cleanup keeps the three highest stable versions plus
`current` and any release some process is executing or has mapped. An absent
package is optional; an incomplete or unsafe tree fails. A busy or invalid tree
does not block checking its peer. Non-dumpable processes (sshd,
ssh-agent, sd-pam, smbd) hide `/proc/<pid>/exe` and `maps` even from `pi`, so the
probe needs root and refuses cleanup if any process cannot be inspected. It holds
both the `flock` and `lockf` forms of the installer's `install.lock`, validates
package manifests, ownership and devices, refuses any mount at or beneath a target
per `/proc/self/mountinfo` (which also reveals same-device bind mounts), and
deletes through directory descriptors that stop at any other filesystem, like
`rm --one-file-system`. The remaining gap is a same-device bind mount created
by root inside `~/.codex` after the final mountinfo check. A held installer lock (exit 75) is reported as a skip; any
other failure makes the tidy exit nonzero after it still cleans apt. `apt-get
clean` runs when already root or passwordless `sudo -n` is available; otherwise it
prints a skip message. The installed root-crontab entry runs Sunday 01:30, outside the 03:00–08:00 backup
window:

```cron
30 1 * * 0  "$scripts/rootfs_tidy.sh" --apply  >> "$cronlog/rootfs_tidy.log" 2>&1
```

The same run (`rootfs_tidy_sweeps.py` beside it, `/usr/bin/python3 -I`, executed as `pi`
through `setpriv` when root; preview with
`/usr/bin/python3 -I /home/pi/scripts/rootfs_tidy_sweeps.py`) also, each as a dry run
without `--apply`:

- deletes `~/.codex/archived_sessions/**` files older than 30 days, logging each file
  and the run's count and bytes. Live `sessions/` and `migration-backups/` are never
  opened;
- removes `obd-things/tmp/suite-*` directories idle for 2 days and `tmp/playwright-*`
  siblings idle for 14 days (idle: nothing inside newer than that; `tmp/playwright`,
  `playwrightNotes`, `compute`, `vehicle_data`, `captures` are never matched);
- deletes `~/.npm/_cacache` and `~/.cache/pip` files older than 30 days;
- lists files over 200 MB and older than 14 days in `~/Documents/agent-shared`
  (report only; nothing is deleted).

Every root is opened from `/` one component at a time without following symlinks, so a
root reached through a symlink is skipped, and every later open, lstat, unlink and rmdir
is relative to a held directory descriptor: swapping a directory for a symlink mid-run
cannot redirect a delete. Nothing on another filesystem is entered or deleted (a
different `st_dev`, or a mount point listed in `/proc/self/mountinfo`, which also
catches bind mounts); a scratch directory with a mount anywhere below it is kept whole.
Each file is lstat-ed again just before the unlink: a session or cache file must still
be the same inode with the same size and mtime, and a scratch-directory entry must still
be older than the cutoff (otherwise the directory stays). The one unchecked window is an
entry swapped, or appended to by a writer that already held it open, between that check
and the unlink; every candidate has been idle for the whole cutoff. A root that exists
but cannot be opened fails the run.

Nothing in the sweep writes to EXFAT512. A one-time archive made on 2026-11-14 is kept at
`/mnt/EXFAT512/vanpi-root-archive/`: `codex/`, `pcan/` and `agent-shared/`, each with a
`MANIFEST.sha256`.

`/etc/logrotate.d/rsyslog` comes from `pi/configs/logrotate-rsyslog` (installed by
`pi/sync_scripts.sh` through `install_logrotate_conf.sh`, which requires `logrotate -d`
exit 0 on a root-owned copy of the effective config, then stages a verified, fsynced
copy in `/etc` and renames it into place, so a failed write leaves the old file): syslog and auth.log rotate weekly
or at 50 MB, whichever first (logrotate runs daily), and compress immediately.

Compute job history is pruned separately at Sunday 01:45 by van-compute's own
`prune` subcommand, run as `pi`. Its retention rules (cited jobs kept, uncited
`done` jobs after 60 days, `failed` after 31) live in the van-compute repository's
`pi/docs/compute/VAN_COMPUTE.md`. Preview with
`/home/pi/van_compute/scripts/van_compute.py prune`.

```cron
45 1 * * 0  su pi -c "/home/pi/van_compute/scripts/van_compute.py prune --apply" >> "$cronlog/van_compute_prune.log" 2>&1
```
