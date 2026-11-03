# Offsite USB backups

Seed an encrypted restic repository on a borrowed USB HDD while it is attached to
vanpi, then move it to a friend's Synology. This v1 only accepts a local labeled
filesystem. There is no NAS watcher, remote `rest:` target, automatic schedule,
retention/prune, or automatic retry. A 3 TB drive can be priority 10; a later
14 TB drive priority 20. Capacity figures are nominal, not a fit guarantee.

## Install

Requires Debian 12 arm64, Python 3.11, util-linux (`lsblk`, `findmnt`, `wipefs`,
`sfdisk`), e2fsprogs, udev, Samba JSON `smbstatus`, curl and bzip2. The existing
backup cohort, disk policy/mount tools, ntfy sender and Samba preexec drain gate
must already be installed. New files travel in both targeted backup deployment
plans; deployment is separate from running this tool. Do not copy one entry
script without its sibling modules. See [deployment](../deployment.md) and
[backup concurrency](CONCURRENCY.md).

On the Pi, after the reviewed cohort has been deployed:

```sh
sudo /home/pi/scripts/backup/install_restic.sh
```

The installer pins restic **0.19.1**, archive `restic_0.19.1_linux_arm64.bz2`,
from the [official release](https://github.com/restic/restic/releases/tag/v0.19.1).
Its archive SHA-256 from that release's `SHA256SUMS` is
`a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465`.
The installed binary is also hash-verified; a matching installation needs no
network download. Hash mismatch aborts before replacement. This script does not
install apt packages or start a service.

## Prepare a disk — destructive

Park the van. Plug in the borrowed disk and inspect it, keeping the known disks
attached so their identities can be checked. Never substitute a guessed `/dev/sdX`.

```sh
ls -l /dev/disk/by-id/
lsblk -b -o NAME,MODEL,SERIAL,SIZE,FSTYPE,LABEL,MOUNTPOINTS
```

Replace `usb-REPLACE_WITH_EXACT_DISK_ID` below with the actual whole-disk by-id
name, not a `-part1` link. First omit confirmation: the tool prints model, serial,
size and partition layout, then exits nonzero **without erasing anything**.

```sh
sudo /home/pi/scripts/backup/offsite_backup.sh prepare-disk \
  --device /dev/disk/by-id/usb-REPLACE_WITH_EXACT_DISK_ID \
  --label offsite-3tb --priority 10
```

Only after reviewing that layout, supply the exact serial it printed:

```sh
sudo /home/pi/scripts/backup/offsite_backup.sh prepare-disk \
  --device /dev/disk/by-id/usb-REPLACE_WITH_EXACT_DISK_ID \
  --label offsite-3tb --priority 10 --erase-confirm REPLACE_WITH_EXACT_SERIAL
```

This destroys that disk's existing contents. It refuses mounted partitions,
swap, root/boot devices, duplicate/empty serials, non-USB disks, disk-policy or
hotspare labels, known offsite labels, reused labels and ambiguous discovery.
Existing mount-directory contents are never removed. The new label must be
1–16 ASCII letters/digits/dot/underscore/hyphen, starting with a letter or digit.

It creates one GPT Linux partition and ext4 with zero reserved percentage,
explicitly excluding `orphan_file` and `metadata_csum_seed`. Remaining features
are Debian defaults. Actual compatibility with this friend's NAS must still be
tested: Synology model/kernel support can vary. It mounts `/mnt/offsite-3tb`,
initializes `/mnt/offsite-3tb/vanpi-restic`, then writes the marker and private
registration. A failed preparation is **not** a registered usable backup; inspect
its journal/error and disk layout before any repeat erasure. Preparation leaves
the verified mount available for the seed run.

For the 14 TB drive, use its own unique by-id and serial, label `offsite-14tb`
and `--priority 20`. Do not reformat an existing registered drive to refresh it.

## Save the password independently

The first preparation generates the shared password with Python `secrets` at
`/root/.config/restic/vanpi-offsite.password` (root, mode 0600). Later drives use
the same password. It is never put into arguments, status, markers or logs.
Do not regenerate it: losing it loses access to every offsite drive. The tool
refuses to create a replacement when repositories are already registered.

From the Mac, copy it into a new private file without printing it:

```sh
umask 077
ssh vanpi.lan 'sudo cat /root/.config/restic/vanpi-offsite.password' \
  > "$HOME/vanpi-offsite.password"
chmod 600 "$HOME/vanpi-offsite.password"
pbcopy < "$HOME/vanpi-offsite.password"
```

Paste into the password manager, verify the saved entry, clear the clipboard
(`pbcopy < /dev/null`), then remove only that temporary copy:
`rm "$HOME/vanpi-offsite.password"`. Keep the native Time Machine image password
and Borg passphrase independently too; restic encryption does not replace them.

## Run detached, with journal logging

A full seed may take 10–20 hours. The exclusive legacy backup lock excludes all
other backup/clone/cloud/maintenance jobs for the entire run. There is no new
lock name or automatic priority preemption. If busy, retry after the current job
finishes; do not delete lock files. Ignition-on refuses new work and interrupts
a running job through the existing disk-abort path.

For Time Machine, on the Mac first disable new scheduled backups:

```sh
sudo tmutil disable
tmutil status
hdiutil info
```

Let an active backup finish. If the image remains attached, identify the exact
Time Machine image device in `hdiutil info` and cleanly detach it with
`hdiutil detach /dev/diskN` (replace `diskN` with that verified device; never
force it). `tmutil disable` alone does not stop an in-progress backup or detach
an image. The Pi refuses any open image handles, rather than forcing them closed.

On the Pi:

```sh
sudo systemd-run --unit=vanpi-offsite --collect --service-type=exec \
  --property=KillMode=mixed --property=TimeoutStopSec=120 \
  /home/pi/scripts/backup/offsite_backup.sh backup --label offsite-3tb
```

`KillMode=mixed` sends TERM to the Python owner first, allowing it to stop/reap
restic, remove only its Samba gate, release owned source mounts and publish the
outcome. The systemd cgroup remains the final bound on shutdown. SSH disconnects
do not stop the unit. Use the same command to resume after an interruption; restic
reuses uploaded chunks. No source is marked successful on partial-copy exit 3.

To select sources, append `--only time-machine`, `--only exfat512`,
`--only movingparts` or `--only borg`; repeat `--only` for several.

```sh
journalctl -u vanpi-offsite -f
sudo /home/pi/scripts/backup/offsite_backup.sh status
sudo systemctl stop vanpi-offsite
```

Status JSON contains source, bytes done/total, percent, mean rate, ETA, timestamps,
last result and individual source outcomes. Rate/ETA are estimates from processed
bytes, not physical disk throughput. Percent stays below 100 until successful
snapshot confirmation. A stopped/killed process does not make old progress live:
check `updated`, `finished` and the unit state. One final ntfy reports each source
and real errors; notification failures also appear in status/journal.

After the Time Machine source has completed or the run stopped, re-enable its
normal Mac schedule:

```sh
sudo tmutil enable
```

## Sources and consistency

| Tag | Paths | Treatment |
| --- | --- | --- |
| `time-machine` | `/mnt/mbp2tbkup/m4mac0.sparsebundle` | Compression off; owned nonempty Samba drain blocks new connections, zero handles required before closing idle trees; gate/handles/mount monitored; source inventory rechecked at end; transient `lock` excluded. |
| `exfat512` | `/mnt/EXFAT512` | Excludes `van-tv/` like the existing EXFAT snapshot, plus `System Volume Information/` and all `vanpi-network/`. |
| `movingparts` | `/mnt/movingparts/Jake`, `music`, `guitar_tab_archive` | One snapshot containing those three directories, not the movie/torrent tree. |
| `borg` | `BORG_REPO` from `backup_conf.sh` (currently `/mnt/bigboi/borg/vanpi-encrypted`) | Global maintenance lock keeps cooperating Borg jobs out; mounts bigboi through the exact-label helper and releases a mount it created. |

**Diagnostic recorder history under `vanpi-network/` is deliberately omitted in
v1**: it includes a live SQLite database. No live database/WAL copy and no online
snapshot staging are attempted. The existing `exfat_snapshot.sh` is unchanged.
Other EXFAT/media files can change while copied; these are file backups, not a
cross-filesystem application transaction. Stop editing important source files
while seeding. Restic's unreadable/changed-file errors are failures, not success.

Every source gets its own snapshot with `--host vanpi`, its source tag,
`--one-file-system` and `--no-cache`. Restic starts it with `offsite-incomplete`;
that tag is removed only after restic succeeds and consistency checks pass.
Interrupted/rejected snapshots, if published by restic, retain the incomplete
tag and are **not recovery points**. No automatic deletion or retention runs.
No restic cache fills the Pi rootfs; in-memory index cost still depends on repo size.

All source mounts are verified against the exact expected label/source, including
nested source paths. A mount created by this run is released through the existing
backup disk helper. Pre-existing source mounts stay mounted. A target mounted by
the backup run is unmounted at exit; on SIGTERM even a pre-existing verified offsite
target is unmounted. The offsite disk is not added to automatic HDD policy or
spindown lists; unplug it only after a verified ordinary unmount.

## Check and hand over

A seed is not verified merely because its snapshots finished. Run the structural
check, then a data sample (or cycle disjoint subsets to cover the whole repository):

```sh
sudo /home/pi/scripts/backup/offsite_backup.sh check --label offsite-3tb
sudo /home/pi/scripts/backup/offsite_backup.sh check \
  --label offsite-3tb --read-data-subset 5%
```

`1/10` through `10/10` cover every pack once across ten checks; `5%` is a random
sample, not full coverage. A long check can use the same transient-unit prefix as
backup, replacing its subcommand and options. Check also takes the exclusive lock,
verifies registration/repository ID and uses no cache. It never initializes a repo.
Perform a small restore test to a separate destination before relying on the drive.

Before unplugging, verify the mount and unmount it if still mounted:

```sh
findmnt -M /mnt/offsite-3tb -o SOURCE,TARGET,FSTYPE
sudo umount /mnt/offsite-3tb
findmnt -M /mnt/offsite-3tb
```

The last `findmnt` should exit 1 with no output. Any other error is not proof of
unmount. Do not use force/lazy unmount or recursive removal to work around a busy
disk. The marker is `.vanpi-offsite` at the filesystem root:

```json
{"schema":1,"label":"offsite-3tb","repo_id":"<64 hex characters>","repo_path":"vanpi-restic","priority":10,"created":"<UTC timestamp>"}
```

It has no password. A future NAS watcher may use the highest-priority marked disk;
this tool does not implement that watcher or configure the NAS. The Pi's root-only
`/var/lib/vanpi-offsite/repos.json` binds labels to these exact marker records and
restic IDs. Backup/check refuse mismatches and never call `restic init`. Preserve
this state with the password during Pi recovery; do not hand-edit records merely
to silence an unknown-repository refusal. The local-repository adapter is the
extension point for a separately reviewed remote backend in v2.

A normal failure or TERM clears only this process's populated Samba gate. SIGKILL
or power loss cannot run cleanup (reboot clears `/run`). If a gate remains without
a live unit, inspect `/run/lock/vanpi-samba-drain/mbp2tbkup` and its owner/PID before
removing it; never remove a shutdown gate or another capture worker's gate.
