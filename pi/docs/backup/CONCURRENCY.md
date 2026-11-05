# Backup concurrency model

Backup jobs on vanpi own resources explicitly instead of sharing one global
lock. A job declares the disks it uses, the data sets it mutates, and whether it
needs the single iCloud upload slot. Independent jobs overlap; conflicting jobs
fail closed or defer. The legacy global lock remains for consumers that were
never audited for overlap.

## Lock registry

All locks are advisory `flock(2)` locks on files pre-created at boot by
`pi/tmpfiles.d/vanpi-backup.conf` as `root:pi 0660` inside
`/run/lock/vanpi-backup/` (`root:pi 0770`). The kernel drops every lock the
moment its last holder exits, so a crashed job can never leave a stale lock;
PID records inside job locks are advisory and are verified against `/proc`
before anyone acts on them. `/run` is tmpfs, so a reboot recreates the registry
empty. Acquisition fails closed when a lock file or the directory is missing,
symlinked, or has unexpected ownership or mode.

`backup_locks.py` owns the resource names and job labels. Run
`python3 -m pi.generate_backup_lock_registry` after changing them; it renders
the shell declarations in `backup_lock_registry.sh` and the tmpfiles list.
Both targeted installers and broad sync run the read-only `--check` before
staging; targeted installers repeat it on the transferred payload. Shell jobs
source the generated declarations directly. Lock paths and modes are unchanged.

| Lock | Mode | Holder | Meaning |
| --- | --- | --- | --- |
| `job-<kind>.lock` (`borg`, `exfat`, `pi-cloud`, `time-machine`, `clone`, `offsite`) | exclusive | the job parent; PID recorded (`offsite` also held, without a PID record, by every exclusive legacy holder) | one instance per job kind; the stop/abort target |
| `disk-<label>.lock` (`bigboi`, `hdd1tb`, `mbp2tbkup`, `movingparts`, `EXFAT512`, `FORT-MPLS-5TB`: every label `disk_policy.sh` manages) | shared by jobs, exclusive by lifecycle | every job using the disk, inherited by its children | the disk cannot be unmounted or spun down while held |
| `data-borg-repo.lock` | exclusive | Borg mutation or a consistent repository copy | repository-level mutual exclusion |
| `cloud-upload.lock` | exclusive | one cloud worker run | exactly one iCloud uploader (Pi or Mac) |
| `/run/lock/vanpi_backup.lock` (legacy) | shared by migrated jobs and by an offsite run while it copies its code snapshot; exclusive by legacy consumers; PID recorded by exclusive holders only | restore, hotspare initialization, offsite `prepare-disk`, deployment fences | an exclusive holder excludes every job; any running migrated job excludes exclusive holders |

Offsite runs never hold the legacy lock past their snapshot copy, so the
exclusive legacy consumers' `acquire_job_lock` (restore, hotspare
initialization, `prepare-disk`) also takes `job-offsite` exclusively, without
waiting and without a PID record, and keeps it for the caller's lifetime; a
busy lock refuses with "an offsite run is active" and gives the legacy lock
back. A missing registry or `job-offsite.lock` cannot have admitted an offsite
run (its launcher requires the pre-created inode), so it is skipped, never
created; unsafe metadata fails closed. The deployment fence takes the legacy
lock alone: leaving `job-offsite` out is what lets an installer publish during
a seed.

Shell jobs use `backup_locks.sh` (sourced by `backup_conf.sh`): fd 9 is the
legacy lock, fd 10 the job lock, other fds are allocated per disk, data set or
slot. Python workers inherit those descriptors from their shell entry point and
use `backup_locks.py` for descriptor checks and status enumeration. `exec` keeps
the descriptors; children of a shell job inherit them (so emergency termination
can target the whole tree by lock file), while Python's `subprocess` children do
not.

Both Python `--run` entries validate admission before creating a state directory,
consuming a queued resume, recording an attempt or launching a client.
`backup_admission.py` captures `VANPI_BACKUP_JOB_KIND`, `VANPI_BACKUP_LOCK_DIR`,
`VANPI_BACKUP_LEGACY_LOCK_FD`, `VANPI_BACKUP_JOB_LOCK_FD`,
`VANPI_BACKUP_CLOUD_SLOT_FD` and the comma-separated `label=fd` records in
`VANPI_BACKUP_DISK_LOCK_FDS`. Missing metadata, duplicate/wrong descriptors,
unsafe registry metadata, the wrong disk set or absent/wrong lock modes refuse
the run. Linux `/proc/self/fdinfo` proves the hold belongs to that open file
description; `require_held` never acquires or upgrades a lock.

The immutable bundle remains in a scoped worker context and is passed explicitly
to the forked verifier. Clients use its cached descriptors and inode identities;
they do not rediscover admission from environment variables or scan `/proc`.
A temporary Borg repository descriptor is separately checked and passed during
the repository copy. The context resets even when a run fails; any future
client-launching thread must receive and bind the bundle explicitly. Priority,
ignition and uplink guards still run at their existing repeat points.

Pi `--preflight`, `--status`, `--login` and `--verify-login` are outside cloud-run
admission; only login and its upload/download canary use the explicit account
client scope. Mac `--capture-request`, `--mac-present`, `--mac-present-v2`,
`--claim-capture-stop`, `--capture-ready`, `--release-capture`, `--status`,
`--staging-gc-plan` and `--staging-gc-apply` also remain outside it. Staging GC
still independently asserts its job hold before planning or deleting objects.

## Ownership per job

| Job | Entry point | Legacy | Job lock | Disks (shared) | Data (exclusive) | Cloud slot |
| --- | --- | --- | --- | --- | --- | --- |
| Borg | `pi_backup.sh` | shared | `borg` | `bigboi` whole run; `movingparts` until the media mirror finishes | `borg-repo` from `borg create` through prune/compact/check; `job-clone` during the hotspare phase (skipped if busy) | no |
| EXFAT snapshot | `exfat_snapshot.sh` | shared | `exfat` | `EXFAT512` (source, whole run); `hdd1tb` until its own unmount/spindown | none (only this job writes the snapshot tree) | no |
| Pi cloud | `icloud_backup.sh --run` | shared | `pi-cloud` | `bigboi` whole run | `borg-repo` only during the `borg with-lock` repository copy; busy means defer | yes, whole run |
| Mac cloud | `time_machine_icloud.sh --run` | shared | `time-machine` | `mbp2tbkup` whole run | capture gate (unchanged Samba drain handshake) | yes, whole run |
| Mac staging GC | `time_machine_icloud.sh --staging-gc-*` | shared | `time-machine` | `mbp2tbkup` | none | no |
| Hotspare clone | `clone_now.sh` | shared | `clone` | none (cards use the existing per-device `lock_disk`) | none | no |
| Offsite backup / check / receipts / restore-test (local or `--remote`) | `offsite_backup.sh` | shared, non-blocking, only while the code snapshot is copied | `offsite`, taken first (fd 10, PID record; the Python parent after `exec`) | a local target's label for the whole command; each source's label for its source phase, taken before mounting; `restore-test`: the disk holding `--target` (`findmnt -T`), or none on the root filesystem | `borg-repo` during the `borg` source only (busy: the source moves to the end, then waits up to 3 h, polling once a second and holding no `bigboi` lock until it has the repository); the Time Machine image keeps its Samba gate | yes, whole command (one named policy constant) |
| Offsite prepare-disk | `offsite_backup.sh prepare-disk` | exclusive, whole command | `offsite`, exclusive without a PID record (via `acquire_job_lock`) | all (implied) | all (implied) | no |
| Restore | `restore_from_borg.sh` | exclusive | `offsite`, exclusive without a PID record | all (implied) | all (implied) | no |
| Hotspare init | `new_hotspare.sh` | exclusive | `offsite`, exclusive without a PID record | all (implied) | all (implied) | no |
| Deployment fence and its recovery | both targeted cloud installers; a transaction's journaled recoverer | exclusive, non-blocking, PID recorded | none, never `offsite` | all but a running offsite run's | all but a running offsite run's | all but a running offsite run's |
| SMART watch | `smart_watch.sh` | none | none | each mounted HDD for one `smartctl` read; busy means skip | none | no |

## Conflict matrix

| | Borg | EXFAT | Pi cloud | Mac cloud | Clone | Offsite run | Restore / init / prepare-disk |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Borg | job lock | overlap | overlap except the repository copy window: Borg waits up to `BORG_REPO_LOCK_WAIT_SECONDS` then fails; Pi cloud defers to its next timer | overlap | overlap except Borg's hotspare phase (`job-clone`) | overlap except the `borg` source (`borg-repo`): Borg waits as for Pi cloud; offsite defers the source, then waits up to 3 h and fails only that source | excluded |
| EXFAT | overlap | job lock | overlap | overlap | overlap | overlap (`EXFAT512` shared by both) | excluded |
| Pi cloud | see above | overlap | job lock | **cloud slot**: the later starter exits before recording an attempt; timers and resume paths retry | overlap | cloud slot: the Pi worker exits before recording an attempt while any offsite command runs | excluded |
| Mac cloud | overlap | overlap | cloud slot | job lock | overlap | cloud slot, as for Pi cloud; while offsite reads the image, its Samba gate also defers a capture | excluded |
| Clone | see above | overlap | overlap | overlap | job lock, per-card `lock_disk` | overlap | excluded |
| Offsite run | see above | overlap | cloud slot | cloud slot | overlap | `job-offsite` | excluded (`job-offsite`, and the legacy lock during the snapshot copy) |
| Restore / init / prepare-disk | excluded | excluded | excluded | excluded | excluded | excluded | legacy exclusive |

"Overlap" accepts ordinary I/O contention. Mac Time Machine (Samba) is not a
backup job; the capture handshake and share drain are unchanged. Offsite runs
take no priority admission: `acquire_job_lock`'s `--admit disk` call always
admits, so holding `cloud-upload` directly keeps today's behavior that no
iCloud upload runs during offsite work.

## Lifecycle, cancellation and recovery

- `abort_backup.sh [--emergency] [LABEL ...]` (called by `umount_disks.sh` with
  the labels it is about to unmount, or with `--spindown` every attached HDD
  it will stop, and by stale-mount recovery with the affected labels)
  sends TERM to every job parent whose process holds that disk lock, plus any
  legacy exclusive holder, then waits for each disk lock to become free
  (exclusive `flock -w`). With `--emergency` (ignition) the wait is bounded and
  remaining holders of those lock files, including inherited children, receive
  KILL. Without labels, every registered disk is drained (the historical
  "stop everything" behavior for callers that cannot name labels).
  An offsite run is found like any job, through `job-offsite.lock`'s PID record
  (its Python parent, `exec`ed by the launcher, holds the phase disk
  descriptors). `umount_disks.sh --spindown` drains `FORT-MPLS-5TB` with the
  other attached HDDs, so ignition stops a local seed and then unmounts and
  spins the drive down. A remote run reading only `EXFAT512` (never unmounted
  on spindown) is not drained; its own ignition guard stops it. Draining a disk
  the run does not hold leaves it alone. `prepare-disk` is still stopped as a
  legacy exclusive holder.
- After a successful drain `umount_disks.sh` (its sourced `umount_disks_fence.sh`)
  claims every disk it is about to
  unmount or spin down exclusively (`hold_backup_disks_exclusive`) and keeps
  those descriptors until it exits, so no job can start using a disk between
  the drain and the unmount or spindown. A job that starts meanwhile fails
  closed ("<label> is being unmounted or drained") and its scheduler retries
  later. The spindown fence covers attached disks that are not mounted: a
  job that passed its ignition check holds its disk locks before it mounts,
  so the ignition path must stop it rather than let it spin the disk back up.
  Because the fence can only stop lock holders, each local job re-checks the
  ignition flag after taking its locks and before mounting anything (Borg
  right after `acquire_backup_job`, EXFAT before and after its mount): a flag
  raised before that check defers the job, and one raised after it finds the
  job holding its disks, where the drain reaches it.
- The fence and both manual/automatic stale-mount recovery share `backup_lifecycle_claim`:
  validate every label before draining, then retain exclusive disk claims in
  the caller through its mutations. `stale_mount_recovery.sh` owns one recovery
  operation. Policy sources the mount library inside its bounded subshell and
  supplies its consumer-shutdown callback; manual `mount_disks.sh --recover-stale`
  uses the same operation, retaining normal busy-unmount refusal. Claims stay in
  that shell through mutation; no caller-supplied ownership flag is trusted.
  Original label/target/source records remain the permission boundary, including
  an explicitly empty set. Recovery refuses added targets or changed
  sources before any unmount and rechecks each original source immediately
  before its ordinary unmount. The policy mutex remains held independently.
- Timed waits (`acquire_backup_lock <class> <name> <seconds>`, used for the Borg
  repository) poll a non-blocking `flock` once a second so the job's USR1/TERM
  traps keep working while it waits.
- Python workers pass their admission bundle's descriptors (and any checked
  repository-copy descriptor) to their clients, so an orphaned rclone or borg process
  still holds its job's disk, slot and repository locks and is found by the
  emergency kill-by-lock-file path.
- `abort_backup.sh --user borg|exfat` reads the job lock's PID record, verifies
  the process is alive, runs the expected tool and holds that job lock, sends
  USR1, and waits for the job lock to release. Other jobs are untouched.
- A job's cleanup releases only its own descriptors. A job that mounted a disk
  unmounts it only when `release_backup_disk_if_unused` proves no other job holds
  that disk; otherwise it leaves the mount for the remaining user.
- A reboot empties the registry. A killed job frees its locks immediately; a
  surviving orphaned child keeps the disk lock held, so the drain fails closed
  (or the emergency path kills it by lock file). For that reason every release
  only closes the descriptor and never unlocks it explicitly: an explicit
  unlock would strip the child too, while a close ends the lock only with the
  last descriptor on that open file.
- Deployment fences take the legacy lock exclusively and non-blocking, so they
  wait for every migrated job and refuse while restore, hotspare
  initialization or `prepare-disk` runs. Like other exclusive holders they
  write their PID into it, so a drain TERMs an installer into its rollback
  (see "Interrupted deployments" below). An offsite run holds the legacy lock
  only while it copies its code snapshot (seconds), so the fence fails fast
  only in that window and otherwise publishes while the run continues on its
  frozen copy.
- Under the fence, both installers refuse to publish a staged registry that
  lacks any lock (job, disk, data or cloud) currently held in the live
  registry, since drain, stop and status code find holders only through it.
  Holders are read from `/proc/locks` by inode, never probed: even a momentary
  exclusive probe would fail a starting job's non-blocking shared disk hold.

## Interrupted deployments

The targeted installers publish the cohort one file at a time. A SIGKILL (the
emergency drain's, after its grace) skips their EXIT-trap rollback and could
leave old and new files mixed, so every transaction is journaled durably:

- **Journal.** `/var/lib/vanpi-backup-deploy/` (root, on the root filesystem;
  `/tmp`, where stages live, is emptied at boot) holds one root-only
  directory per transaction: `previous/manifest` and `previous/tree/` (the
  saved originals), `active-timers`, and `recovery/`, a copy of
  `backup_deploy_recovery.sh` with every library it sources. Beside them,
  `pending` names the open transaction. It is written with fsync and an
  atomic rename before the first live change (`phase=preparing`, before any
  timer stops), switched to `phase=publishing` only once the whole manifest
  and tree are durable. After destination files and directory entries have been
  flushed and platform checks succeed, `phase=committed` records the decision
  without removing the marker. Finalization restores recorded timers and only
  then clears pending. Temporaries carry the installer's PID, so recovery finds
  even ones a killed run never recorded. The newest five transactions are kept
  for manual rollback.
- **Jobs refuse.** Admission checks pending and bootstrap maintenance before
  opening the legacy lock and again **under the acquired hold**. This covers
  `acquire_backup_job` (Borg, EXFAT, both cloud workers, clones),
  `acquire_job_lock` (restore, hotspare init, `prepare-disk`) and offsite snapshot
  startup. Python cloud admission rechecks after validating inherited descriptors.
  Direct `clone_to_sd.sh` joins admission; children validate and retain their
  parent's shared or exclusive hold rather than reacquiring it. The fence and
  disk lifecycle never consult the job guard, so a drain or unmount never waits
  on a transaction.
- **Recovery.** The independent root-owned dispatcher at
  `/usr/local/lib/vanpi-backup-deploy/recover` runs hourly from
  `/etc/cron.d/vanpi-backup-deploy`, outside cohort rollback. The window and
  each installer also check pending before proceeding. After checking ownership
  the dispatcher runs the transaction's own journaled recoverer as root. It takes
  the legacy fence without waiting and records its PID. Preparing/publishing
  transactions restore the guarded predecessor and reload systemd; committed
  transactions verify the new cohort and **never roll it back**. Both paths
  clean temporaries, restore exactly the recorded active timers and clear pending
  only after destination contents/directory entries are durable. A failure retains
  pending and warns at most daily. A timer already restarted before interruption
  can fire, but job admission still refuses until finalization succeeds.
- **Emergency path.** Unchanged in time: `abort_backup.sh --emergency` TERMs
  the installer through the legacy lock's PID record, waits its 8-second
  grace for the in-process rollback, then KILLs every holder of the lock
  file and moves on to the disks. Whatever the rollback did not finish is
  recovered later, before any job runs, never in the drain.
- **First deployment.** Requires the separately verified maintenance/bootstrap
  phase in [DEPLOY_BOOTSTRAP.md](DEPLOY_BOOTSTRAP.md). Publication ordering alone
  cannot protect old admission functions. The bootstrap minimally guards the
  actual reviewed predecessor and installs the independent trigger before any
  transaction may stop timers. Its launch holds survive interruption and remain
  until explicitly released; it cannot revoke arbitrary already-running old
  shells. Every supported saved predecessor stays guarded. Old installers,
  unguarded rollback targets and broad-sync backup publication are unsupported.

## Offsite code snapshot and its limits

`offsite_backup.sh` stays the stable entry point. For every command but
`prepare-disk` it takes `job-offsite` on fd 10 and writes the PID record, takes
the legacy lock shared without waiting, checks ignition, has
`offsite_snapshot.py create` copy the run's code closure (declared once in
`offsite_config.py`) to `/var/lib/vanpi-offsite/code/<run-id>/` and verify it
against the live files, closes fd 9 and `exec`s the copy's
`offsite_backup.py`. Backup shell code resolves its own helpers beside the
sourced file (`backup_conf.sh`, `mount_disks.sh`), so the copy calls its own
copies while the installed tree's paths are unchanged; data, secrets, state and
the metering CLI stay live.

- Broad sync (`pi/sync_scripts.sh`) is still unfenced. The snapshot narrows its
  window to the seconds of the copy, and the post-copy hash check catches most
  overlaps, but a sync spanning the copy can still leave a mixed snapshot, as
  it can for any job that starts mid-sync.
- The registry guard protects forward deploys only: a targeted installer runs
  its own staged code. Never deploy or roll back the backup cohort to a
  revision without granular offsite locks while `job-offsite` is held; stop
  the run first.
- With pre-granular drain code, the emergency ignition path still frees the
  drive (it kills holders by disk lock file), but the normal path refuses to
  unmount because it cannot find the run.

## Scheduling policy

- Until-verified cloud priority only decides which cloud job may take the cloud
  slot; local jobs and disk jobs are always admitted and never read priority
  state, so corrupt or inaccessible priority storage cannot block them. The former local fairness
  lease, its supervisor and its rollback helper are retired because local jobs no
  longer need a cloud job to yield.
- Timed and indefinite manual pauses (`pause.json`) are unchanged. A queued
  resume is consumed only after the shell entry point holds the job's locks.
- `backup_window.sh` starts Borg and EXFAT concurrently; each keeps its own
  success stamp and once-per-day shortcut.
