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

| Lock | Mode | Holder | Meaning |
| --- | --- | --- | --- |
| `job-<kind>.lock` (`borg`, `exfat`, `pi-cloud`, `time-machine`, `clone`) | exclusive | the job parent; PID recorded | one instance per job kind; the stop/abort target |
| `disk-<label>.lock` (`bigboi`, `hdd1tb`, `mbp2tbkup`, `movingparts`, `EXFAT512`: every label `disk_policy.sh` manages) | shared by jobs, exclusive by lifecycle | every job using the disk, inherited by its children | the disk cannot be unmounted or spun down while held |
| `data-borg-repo.lock` | exclusive | Borg mutation or a consistent repository copy | repository-level mutual exclusion |
| `cloud-upload.lock` | exclusive | one cloud worker run | exactly one iCloud uploader (Pi or Mac) |
| `/run/lock/vanpi_backup.lock` (legacy) | shared by migrated jobs, exclusive by legacy consumers; PID recorded by exclusive holders only | restore, hotspare initialization, deployment fences | an exclusive holder excludes every job; any running job excludes exclusive holders |

Shell jobs use `backup_locks.sh` (sourced by `backup_conf.sh`): fd 9 is the
legacy lock, fd 10 the job lock, other fds are allocated per disk, data set or
slot. Python workers inherit those descriptors from their shell entry point and
use `backup_locks.py` for descriptor checks and status enumeration. `exec` keeps
the descriptors; children of a shell job inherit them (so emergency termination
can target the whole tree by lock file), while Python's `subprocess` children do
not.

## Ownership per job

| Job | Entry point | Legacy | Job lock | Disks (shared) | Data (exclusive) | Cloud slot |
| --- | --- | --- | --- | --- | --- | --- |
| Borg | `pi_backup.sh` | shared | `borg` | `bigboi` whole run; `movingparts` until the media mirror finishes | `borg-repo` from `borg create` through prune/compact/check; `job-clone` during the hotspare phase (skipped if busy) | no |
| EXFAT snapshot | `exfat_snapshot.sh` | shared | `exfat` | `EXFAT512` (source, whole run); `hdd1tb` until its own unmount/spindown | none (only this job writes the snapshot tree) | no |
| Pi cloud | `icloud_backup.sh --run` | shared | `pi-cloud` | `bigboi` whole run | `borg-repo` only during the `borg with-lock` repository copy; busy means defer | yes, whole run |
| Mac cloud | `time_machine_icloud.sh --run` | shared | `time-machine` | `mbp2tbkup` whole run | capture gate (unchanged Samba drain handshake) | yes, whole run |
| Mac staging GC | `time_machine_icloud.sh --staging-gc-*` | shared | `time-machine` | `mbp2tbkup` | none | no |
| Hotspare clone | `clone_now.sh` | shared | `clone` | none (cards use the existing per-device `lock_disk`) | none | no |
| Restore | `restore_from_borg.sh` | exclusive | none | all (implied) | all (implied) | no |
| Hotspare init | `new_hotspare.sh` | exclusive | none | all (implied) | all (implied) | no |
| Deployment fence | both targeted cloud installers | exclusive, non-blocking | none | all | all | all |

## Conflict matrix

| | Borg | EXFAT | Pi cloud | Mac cloud | Clone | Restore / init |
| --- | --- | --- | --- | --- | --- | --- |
| Borg | job lock | overlap | overlap except the repository copy window: Borg waits up to `BORG_REPO_LOCK_WAIT_SECONDS` then fails; Pi cloud defers to its next timer | overlap | overlap except Borg's hotspare phase (`job-clone`) | excluded |
| EXFAT | overlap | job lock | overlap | overlap | overlap | excluded |
| Pi cloud | see above | overlap | job lock | **cloud slot**: the later starter exits before recording an attempt; timers and resume paths retry | overlap | excluded |
| Mac cloud | overlap | overlap | cloud slot | job lock | overlap | excluded |
| Clone | see above | overlap | overlap | overlap | job lock, per-card `lock_disk` | excluded |
| Restore / init | excluded | excluded | excluded | excluded | excluded | legacy exclusive |

"Overlap" accepts ordinary I/O contention. Mac Time Machine (Samba) is not a
backup job; the capture handshake and share drain are unchanged.

## Lifecycle, cancellation and recovery

- `abort_backup.sh [--emergency] [LABEL ...]` (called by `umount_disks.sh` with
  the labels it is about to unmount, and by stale-mount recovery with the
  affected labels)
  sends TERM to every job parent whose process holds that disk lock, plus any
  legacy exclusive holder, then waits for each disk lock to become free
  (exclusive `flock -w`). With `--emergency` (ignition) the wait is bounded and
  remaining holders of those lock files, including inherited children, receive
  KILL. Without labels, every registered disk is drained (the historical
  "stop everything" behavior for callers that cannot name labels).
- After a successful drain `umount_disks.sh` claims every disk it is about to
  unmount exclusively (`hold_backup_disks_exclusive`) and keeps those
  descriptors until it exits, so no job can start using a disk between the
  drain and the unmount or spindown. A job that starts meanwhile fails closed
  ("<label> is being unmounted or drained") and its scheduler retries later.
- Timed waits (`acquire_backup_lock <class> <name> <seconds>`, used for the Borg
  repository) poll a non-blocking `flock` once a second so the job's USR1/TERM
  traps keep working while it waits.
- Python workers pass every inherited lock descriptor to their clients
  (`backup_locks.inherited_lock_fds()`), so an orphaned rclone or borg process
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
  (or the emergency path kills it by lock file).
- Deployment fences take the legacy lock exclusively and non-blocking, so they
  wait for complete quiescence of every job.

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
