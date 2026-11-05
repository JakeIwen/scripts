# Offsite backups

Seed an encrypted restic repository on a borrowed USB HDD while it is attached to
vanpi, then move it to a friend's Synology. Local v1 remains independent of the NAS.
On-demand v2 adds a native append-only receiver and a Tailscale-only `rest:` target;
see [NAS setup and remote operation](OFFSITE_NAS.md). No automatic schedule,
retention/prune, or automatic retry is installed. The actual loaner is the **5 TB
FORT-MPLS-5TB**, priority 10; the later 14 TB drive uses priority 20. Capacity figures
are nominal, not a fit guarantee. A running seed executes from its own frozen code
snapshot, so the backup cohort may be deployed forward during it; see
[deploying during a run](#deploying-during-a-run) for the limits. The preparation
examples below are for a new drive, not the already-seeded loaner.

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
  --label FORT-MPLS-5TB --priority 10
```

Only after reviewing that layout, supply the exact serial it printed:

```sh
sudo /home/pi/scripts/backup/offsite_backup.sh prepare-disk \
  --device /dev/disk/by-id/usb-REPLACE_WITH_EXACT_DISK_ID \
  --label FORT-MPLS-5TB --priority 10 --erase-confirm REPLACE_WITH_EXACT_SERIAL
```

This destroys that disk's existing contents. It refuses mounted partitions,
swap, root/boot devices, duplicate/empty serials, non-USB disks, non-offsite policy
or hotspare labels, already registered offsite labels, reused labels and ambiguous
discovery. Existing mount-directory contents are never removed. The new label must
be 1–16 ASCII letters/digits/dot/underscore/hyphen, starting with a letter or digit.

Before preparing a new label, deploy its explicit entries in disk policy
`OFFSITE_LABELS`, `MANUAL_MOUNT_LABELS` and `HDD_LABELS`, `policyctl`'s managed
labels, and `backup_locks.py`'s disk registry. Regenerate the shell registry and
tmpfiles configuration with `python3 -m pi.generate_backup_lock_registry`, then
use the owning deployment paths. These are safety prerequisites, not auto-discovery:
without them the label is refused or the ignition drain cannot claim its disk
(`test_backup_lock_registry.py` fails while the disk policy and lock registry disagree).
`FORT-MPLS-5TB` already has these entries; a future 14 TB label does not yet.

It creates one GPT Linux partition and ext4 with zero reserved percentage,
explicitly excluding `orphan_file` and `metadata_csum_seed`. Remaining features
are Debian defaults. Actual compatibility with this friend's NAS must still be
tested: Synology model/kernel support can vary. It mounts `/mnt/FORT-MPLS-5TB`,
initializes `/mnt/FORT-MPLS-5TB/vanpi-restic`, then writes the marker and private
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

A full seed may take days (the 14 TB drive about a week). The run holds only the
narrow locks of the resources it touches ([Locks](#locks)): nightly Borg, the EXFAT
snapshot and hotspare clones keep running, while iCloud uploads wait. A busy lock
refuses the command with its cause; retry after that job finishes and never delete
lock files. Ignition-on refuses new work and interrupts a running job through the
existing disk-abort path, which also unmounts and spins down the offsite drive.

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
  /home/pi/scripts/backup/offsite_backup.sh backup --label FORT-MPLS-5TB
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

### Dashboard data

```sh
sudo /home/pi/scripts/backup/offsite_backup.sh dashboard
sudo /home/pi/scripts/backup/offsite_backup.sh rates exfat512
```

Both commands require root and print one sorted-key JSON line. They are read-only:
no backup lock, ignition check, mount, receiver request or state writes. For a remote
target, `dashboard` reads local Tailscale status and route evidence with two-second
bounds; it never runs restic or fetches receiver data merely to render a page. `status`
retains its existing output. `dashboard` returns `{schema: 1, status, history, controls,
availability, evidence: {check, restore_test}}`; history is empty when absent, and missing
evidence is null. Evidence passes through all fields from `remote-check.json` and
`remote-restore-test.json`. The added controls and availability fields are optional
for older consumers. `availability` is null for local targets (the dashboard uses its
validated block-device inventory instead), or `{state, reason}` for remote targets:
`unavailable/not-configured`, `unavailable/network-offline`, `unknown/probe-failed`,
or `unknown/target-unverified`. An online Tailscale peer is never proof that the
receiver has the correct disk; full target verification remains a run-time guard.

The root-private `/var/lib/vanpi-offsite/history.json` retains each label/source's
`latest` terminal outcome and `last_ok` (null until success). Entries include the
source outcome, `finished` epoch and `target_kind` (`local` or `remote`), preserving
previous sources across separate units and previous drive labels. Updates happen
under the run's exclusive `job-offsite` lock with the durable atomic writer. An
unreadable or malformed history is left untouched; status records `history_error`,
and backups continue.
The dashboard command reports that read error rather than pretending history is empty.

`rates <source>` accepts `exfat512`, `movingparts`, `borg` or `time-machine` and
returns `{schema: 1, source, samples: [[epoch_seconds, bytes_per_second], ...]}`.
Each source has a private `rates/<source>.bin` under the same state directory,
using `upload_speed_log.py`'s little-endian `<II` format and 30-day retention.
Samples average logical source bytes processed per second over a minute, **not
network or disk throughput**. A new sampler starts for each source because restic's
counter resets; missing files return empty samples. History and rate failures
never stop a backup. A killed process cannot publish a terminal history entry.

## Manual remote pause and resume

After a remote backup has run with the controls-capable provider, `dashboard.controls`
is `{supported: true, label, revision, paused}`. The label and 32-character revision
are read from that response, not guessed. Otherwise it reports `{supported: false,
reason}`: `local-target`, `older-run` or `unknown`; the dashboard additionally uses
`older-provider` when the installed CLI has no such contract.

```sh
sudo /home/pi/scripts/backup/offsite_backup.sh pause --remote \
  --label LABEL_FROM_DASHBOARD --revision REVISION_FROM_DASHBOARD
sudo /home/pi/scripts/backup/offsite_backup.sh dashboard
sudo /home/pi/scripts/backup/offsite_backup.sh resume --remote \
  --label LABEL_FROM_DASHBOARD --revision FRESH_REVISION_FROM_DASHBOARD
```

Pause durably writes a per-label hold in root-private
`remote-control-LABEL.json`, serialized by `remote-control.lock`. A new remote backup
refuses that hold. An active controls-capable worker polls it through the existing
latched cancellation path, stops/reaps only its own child processes, releases its
Samba gate and resource descriptors, and records interrupted source history. The
control never signals the PID in status and never stops another cloud/local job.
A saved hold means **stop requested**, not proof cleanup has finished.

Resume launches one transient `vanpi-offsite-resume.service` with `KillMode=mixed`,
using the normal launcher and its `resume-run --remote` worker boundary. No schedule,
timer, automatic retry, or timed pause is added. The worker must acquire `job-offsite`,
take its frozen code snapshot and normal resource locks, match the stored repository
ID and original source selection, and pass fresh remote identity/network admission
before clearing the hold. A newer pause invalidates a pending resume by revision.
A busy job, ignition, unconfigured transport, denied uplink or unavailable receiver
leaves the hold intact; inspect `journalctl -u vanpi-offsite-resume` and retry explicitly
after resolving the cause. Later source/Samba failures keep normal failed-attempt
bookkeeping and also require an explicit retry. Restic may reread unfinished sources.
Local seeds and check/receipt/restore commands are intentionally not paused here.
Mac Time Machine settings and existing iCloud policies are never changed.

The dashboard sends form POSTs to `/api/backups/offsite/pause` or `/resume` with exactly
`label` and `revision`, behind the existing origin/header guards. Accepted requests
return HTTP 202; stale, unsupported, refused or unconfirmed requests return 409. It
refreshes authoritative state after either outcome and never replays a mutation.

Deploy the complete scripts cohort (both new modules are in `backup_deploy_plan.sh`
and `FROZEN_IMPLEMENTATION`) before the dashboard consumer. Runs already executing
older snapshots cannot adopt cooperative holds; their controls stay unsupported.
Do not downgrade the provider while remote holds are relied on: older workers can
read the additive status but do not enforce these holds. No NAS setup is performed
by this feature.

## Sources and consistency

| Tag | Paths | Treatment |
| --- | --- | --- |
| `time-machine` | `/mnt/mbp2tbkup/m4mac0.sparsebundle` | Compression off; owned nonempty Samba drain blocks new connections, zero handles required before closing idle trees; gate/handles/mount monitored; source inventory rechecked at end; transient `lock` excluded. |
| `exfat512` | `/mnt/EXFAT512` | Excludes `van-tv/` like the existing EXFAT snapshot, plus `System Volume Information/` and all `vanpi-network/`. |
| `movingparts` | `/mnt/movingparts/Jake`, `music`, `guitar_tab_archive` | One snapshot containing those three directories, not the movie/torrent tree. |
| `borg` | `BORG_REPO` from `backup_conf.sh` (currently `/mnt/bigboi/borg/vanpi-encrypted`) | Holds `data-borg-repo` exclusively, so Borg jobs never write it mid-copy; a busy repository moves this source to the end of the run ([Locks](#locks)). Mounts bigboi through the exact-label helper and releases a mount it created. |

**Diagnostic recorder history under `vanpi-network/` is deliberately omitted in
v1**: it includes a live SQLite database. No live database/WAL copy and no online
snapshot staging are attempted. The existing `exfat_snapshot.sh` is unchanged.
Other EXFAT/media files can change while copied; these are file backups, not a
cross-filesystem application transaction. Stop editing important source files
while seeding. Restic's unreadable/changed-file errors are failures, not success.

Every source gets its own snapshot with `--host vanpi`, its source tag,
`--one-file-system` and `--no-cache`. Restic starts it with `offsite-incomplete`.
Local v1 removes that tag only after restic succeeds and consistency checks pass;
remote v2 retains it and publishes a separate encrypted completion receipt instead
([receipt rules](OFFSITE_NAS.md#completion-and-recovery)). Interrupted/rejected snapshots
without completion evidence are **not recovery points**. No automatic retention runs.
No restic cache fills the Pi rootfs; in-memory index cost still depends on repo size.

All source mounts are verified against the exact expected label/source, including
nested source paths. A source mount created by this run is released at the end of
that source's phase (if the run is stopping, at its end instead), and only when no
other job holds the disk. Pre-existing source
mounts stay mounted. A target mounted by the backup run is released at exit; on
SIGTERM even a pre-existing verified offsite target is released. Offsite drives are manually mounted, like bigboi, and
explicitly included in HDD ignition unmount/spindown policy and the disk lock
registry. `FORT-MPLS-5TB` is already configured this way. Unplug only after a
verified ordinary unmount; never rely on the absence of an active backup alone.

## Locks

The run conflicts only with jobs that touch the same resources. The registry,
conflict matrix and drain behavior are in [backup concurrency](CONCURRENCY.md).

| Command | Whole command | Per source phase |
| --- | --- | --- |
| `backup` (local target) | `job-offsite` exclusive (fd 10, PID record); iCloud slot; target disk shared | source disk shared; `data-borg-repo` exclusive for `borg` |
| `backup --remote` | `job-offsite`; iCloud slot | same as local |
| `check` (local) | `job-offsite`; iCloud slot; target disk shared | none |
| `check --remote`, `receipts` | `job-offsite`; iCloud slot | none |
| `restore-test` | `job-offsite`; iCloud slot; the disk under `--target`, shared | none |
| `prepare-disk` | the exclusive legacy lock (also `job-offsite`), as before | none |

- `offsite_backup.sh` takes `job-offsite`, then the legacy lock shared only while it
  takes the [code snapshot](#code-snapshot): a restore, hotspare initialization,
  `prepare-disk` or deployment fence refuses a new run, but none is blocked by a
  running one. The worker then takes the slot and disks itself, always in that
  order, non-blocking except the Borg wait, on existing registry inodes only.
- Disks are locked before mounting, and the ignition flag is rechecked after the
  lock and before the mount, as Borg and EXFAT do. An exclusively held disk lock
  (a drain or maintenance) fails that source, or the whole run for the target.
- Borg: if `data-borg-repo` is busy, the `borg` source moves to the end of the
  run, then waits up to 3 hours, checking TERM and ignition every second. Still
  busy, only `borg` fails, with that cause, and the run result is failed. That
  wait holds no `disk-bigboi` lock, only the job, target and slot locks, so a
  finishing nightly Borg can still unmount bigboi and a bigboi drain during the
  wait leaves the run alone. With the repository held, the run takes bigboi
  shared without waiting (a drain holding it fails `borg` with that cause),
  rechecks ignition and mounts it if needed. An unmount of bigboi during the
  active `borg` phase still stops the run, as it stops Borg itself.
- Children inherit every held descriptor, so an orphaned restic still holds the
  disk and the emergency drain finds it. Release is by close, never unlock. The
  mount helper's `release` is the exception: it inherits only `job-offsite`, so a
  stalled unmount holds no other disk.
- Ending a phase, the worker closes its disk descriptor, then the mount helper
  unmounts what this run mounted only if it can take the disk lock exclusively.
  Helper exit 3 (flock reported another holder: left mounted) and exit 4 (already
  unmounted, e.g. by a drain) are recorded in the status's `cleanup_notes`, not
  errors; any other failure, a flock error included, is a `cleanup_errors` entry
  that fails the run.
- A stopping run (TERM, ignition) holds no disk, data or slot lock while
  anything is released: a phase that ends with a stop leaves its release to the
  end of the run, and a stop that arrives while a release runs closes the run's
  remaining descriptors at once. At the end, every disk, data and slot
  descriptor closes first; only then do the deferred source releases, the target
  release and the ntfy run. The ordinary drain waits only 60 s for the target, an
  unmount can stall, and ntfy can block without an uplink. `job-offsite` (fd 10)
  stays held until exit.
- A stop is final once seen, even an ignition flag that clears again: the run's
  disk, data and slot descriptors may already be closed, so it starts no further
  source. A stop
  seen only during the last source's release still ends the run `interrupted`
  (exit 143), and the target is released as for any stop.
- `restore-test` locks the disk whose mounted filesystem (`findmnt -T`) holds
  `--target`; any target that is neither on such a disk nor on the root
  filesystem is refused, and so is a root-filesystem target inside `/mnt`: it
  sits in an unmounted disk's mount point, which that disk would cover (unlocked)
  once mounted.
- Ignition with `umount_disks.sh --spindown` drains every attached HDD, the offsite
  drive included, so it stops the run, then unmounts and spins the drive down. A
  remote run reading only the `EXFAT512` flash drive is not drained by spindown;
  its own guard (every 5 seconds) stops it on ignition.

Policy constants in `offsite_config.py`, each a one-line change:

- `HOLD_CLOUD_SLOT = True`: every command but `prepare-disk` keeps iCloud uploads
  out, as the old exclusive lock did. Releasing it for local seeds would let
  iCloud run during a week-long seed; that scheduling choice is deferred.
- `EXCLUDED_LOCAL_JOBS = ()`: the out-of-memory fallback. Setting it to
  `('borg', 'exfat')` makes a run also hold those job locks, so nightly Borg and
  the EXFAT snapshot cannot overlap it (they find their job lock busy and exit
  as for an already-running job), and a run refuses to start while either runs.
- `BORG_WAIT_SECONDS`: the Borg-phase wait budget (3 hours).

## Code snapshot

Each mutating run except `prepare-disk` executes from a private copy of its own
code, so installers can publish new code during a days-long seed. While holding
`job-offsite` and the legacy lock shared, the launcher runs `offsite_snapshot.py
create`, which:

1. deletes every directory under `/var/lib/vanpi-offsite/code/` (the exclusive
   `job-offsite` proves no other run is live), walking by descriptor, never
   following a symlink, never crossing a mount or filesystem;
2. copies `FROZEN_IMPLEMENTATION` from `offsite_config.py` (every module and
   helper the run imports, runs or sources) into `code/<run id>/`, laid out like
   `/home/pi/scripts/`, root-owned, directories 0700, files 0700 or 0600;
3. copies `/usr/local/bin/restic` to `bin/restic` after matching the binary
   SHA-256 pinned in `install_restic.sh` (the pin's one declaration), since each
   source starts a new restic;
4. re-hashes every live source and refuses if any changed during the copy;
5. writes `manifest.json` (file and restic hashes, run id, time; the deployed
   revision is null, since the cohort records none yet) and prints the root.

The launcher then closes the legacy lock and execs the snapshot's
`offsite_backup.py`; the worker refuses to run from anywhere else. The run deletes
its snapshot as its last step; a snapshot left by a killed run goes at the next
start. `test_offsite_snapshot_closure.py` rescans the sources and fails when the
declared closure misses a file or frozen code names a live scripts path. Live
data (secrets, state, hooks, `/etc`) and live boundaries (the metering `admit`
CLI, which imports the current package release by design) are declared
separately and never copied.

`status`, `dashboard` and `rates` run live code without locks. They must read
what an older running snapshot writes, and a rollback's older readers what a newer
one writes. So `status.json`, `history.json`, `rates/*.bin`, `repos.json` and the
evidence files are additive-only: new fields are optional and readers ignore
unknown ones. `test_offsite_state_compat.py` checks both directions against v2
(`5ed7311`).

### Deploying during a run

- Broad sync (`pi/sync_scripts.sh`) stays unfenced. The snapshot narrows its
  window to the seconds of the copy, and the re-hash catches most overlaps, but a
  sync spanning the copy can still leave a mixed snapshot, as it can for any job
  starting mid-sync.
- The installers' registry check protects forward deploys only: a targeted
  installer runs its own staged code. Never deploy or roll back the backup cohort
  to a revision without granular offsite locks while `job-offsite` is held; stop
  the run first.
- With pre-granular drain code installed, the emergency ignition path still frees
  the drive (it kills holders of the disk lock file), but the normal path refuses
  to unmount it.

## Check and hand over

A seed is not verified merely because its snapshots finished. Run the structural
check, then a data sample (or cycle disjoint subsets to cover the whole repository):

```sh
sudo /home/pi/scripts/backup/offsite_backup.sh check --label FORT-MPLS-5TB
sudo /home/pi/scripts/backup/offsite_backup.sh check \
  --label FORT-MPLS-5TB --read-data-subset 5%
```

`1/10` through `10/10` cover every pack once across ten checks; `5%` is a random
sample, not full coverage. A long check can use the same transient-unit prefix as
backup, replacing its subcommand and options. Check holds `job-offsite`, the target
disk and the iCloud slot, verifies registration/repository ID and uses no cache. It never initializes a repo.
Perform a small restore test to a separate destination before relying on the drive.

Before unplugging, verify the mount and unmount it if still mounted:

```sh
findmnt -M /mnt/FORT-MPLS-5TB -o SOURCE,TARGET,FSTYPE
sudo umount /mnt/FORT-MPLS-5TB
findmnt -M /mnt/FORT-MPLS-5TB
```

The last `findmnt` should exit 1 with no output. Any other error is not proof of
unmount. Do not use force/lazy unmount or recursive removal to work around a busy
disk. The marker is `.vanpi-offsite` at the filesystem root:

```json
{"schema":1,"label":"FORT-MPLS-5TB","repo_id":"<64 hex characters>","repo_path":"vanpi-restic","priority":10,"created":"<UTC timestamp>"}
```

It has no password. The [NAS watcher](OFFSITE_NAS.md) chooses the unique
highest-priority mounted marked drive. The Pi's root-only
`/var/lib/vanpi-offsite/repos.json` binds labels to these exact marker records and
restic IDs. Local and remote backup/check refuse mismatches and never call
`restic init`. Preserve this state with the password during Pi recovery; do not
hand-edit records merely to silence an unknown-repository refusal.

A normal failure or TERM clears only this process's populated Samba gate. SIGKILL
or power loss cannot run cleanup (reboot clears `/run`). If a gate remains without
a live unit, inspect `/run/lock/vanpi-samba-drain/mbp2tbkup` and its owner/PID before
removing it; never remove a shutdown gate or another capture worker's gate.
