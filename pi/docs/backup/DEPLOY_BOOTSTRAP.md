# Recoverable deployment bootstrap

The targeted Pi and Time Machine installers require a separately verified,
**guarded predecessor**. The independent recovery dispatcher and its hourly root
cron entry are outside the cohort plan: neither publication nor rollback removes
them. Merely installing a dispatcher does not protect old jobs, whose admission
functions do not know the pending marker.

## One-time maintenance boundary

Perform this on a parked van. Keep ignition monitoring, emergency drain,
unmount and spindown enabled throughout. The emergency grace is still 8 seconds;
neither bootstrap nor recovery is an excuse to extend it.

Before applying the bootstrap, the operator must hold new manual/dashboard
backup, restore, clone and offsite starts, and ensure no pre-bootstrap admission
starters remain. A shell that already sourced old code cannot be revoked by a
new marker. Do not assume a process that currently holds no lock is harmless.
Finish existing local/cloud/restore/clone jobs. An identified offsite run already
past its snapshot-copy startup may continue using its frozen code and resource
locks; the bootstrap takes only the legacy fence, not its offsite job lock.
Coordinate with other deployment sessions: their provider changes must be in the
reviewed predecessor and desired next cohort. No other publisher may run during
bootstrap or a pending transaction.

`apply --maintenance-confirmed` first checks the bundle, predecessor hashes,
crontabs, destination paths/ownership and loadable service metadata read-only.
It repeats those checks under the existing non-blocking legacy fence, then saves
and flushes both root/pi crontab originals **before** recording maintenance or
changing either crontab. It comments only recognized backup-start entries and
refuses unknown `/backup/` commands rather than guessing.
Watchdog and disk/ignition jobs remain alone. Four cloud service drop-ins add
`ConditionPathExists=!/var/lib/vanpi-backup-deploy/maintenance`; timers need not be
stopped or masked, and a reboot does not remove the condition. Existing active
workers are not killed. Before guard publication, the tool reloads systemd and
uses its typed `busctl --json=short` Conditions property to require the effective,
non-triggering negated maintenance condition on each cloud service. Merely finding
its own drop-in file or a successful reload is insufficient; missing units or
an overriding condition reset refuse before guard edits.

Still holding the same fence, the tool records durable `bootstrap-publishing`
intent before installing its independent recovery closure/cron or any guard.
It applies minimal guard edits to an explicitly selected predecessor. All live hashes must match that reviewed
predecessor or this bootstrap's exact already-applied replacement. Unrelated
provider additions are retained; changed patch anchors or live hashes refuse.
It never overwrites the host with a historical `fdf473d` cohort.

**An interrupted bootstrap is maintenance, not an automatically recoverable
cohort transaction.** Its durable launch holds stay in place. Rerun the same
saved bundle, verify, then explicitly release. `apply` never releases holds, even
when all readiness checks succeed. Manual/dashboard starts remain an operator
hold until release completes; the tool cannot disable arbitrary root shells or
callers outside the reviewed scheduler/entry paths.

## Prepare and inspect the selected predecessor

From the repository root, set `PREVIOUS` to the reviewed **actual deployed**
revision. Do not substitute HEAD or the historical regression-test revision.
Use a persistent, private local bundle directory that does not already exist:

```sh
PREVIOUS=<reviewed-deployed-commit>
BUNDLE="$HOME/backup-deploy-bootstrap-$PREVIOUS"
python3 -m pi.backup_deploy_bootstrap stage "$PREVIOUS" "$BUNDLE"
```

Inspect `$BUNDLE/plan.json`, the minimally patched guard files and their original
revision before applying. The bundle retains exact original/replacement hashes.
The installed root-private receipt maps reviewed original hashes to those
bootstrap replacements, so the subsequent targeted install can still name the
actual reviewed predecessor with `VANPI_BACKUP_PREVIOUS_REVISION`.

## Apply, verify and explicitly reopen

After establishing the operator boundary above:

```sh
python3 -m pi.backup_deploy_bootstrap apply --maintenance-confirmed "$BUNDLE"
python3 -m pi.backup_deploy_bootstrap verify
python3 -m pi.backup_deploy_bootstrap release --maintenance-confirmed
```

The default target is `pi@vanpi` (LAN when the Pi answers there, else Tailscale).
`verify` is read-only and permits the expected held maintenance state. The remote
apply stage is temporary, but the input bundle and crontab originals are copied
and flushed under `/var/lib/vanpi-backup-deploy/` before any guard publication.
The original local bundle is still needed to rerun the normal apply command.

If apply is interrupted, repeat those three commands with the **same** bundle.
Do not delete `maintenance`, manually uncomment held jobs, remove the condition
drop-ins or use a different bundle to conceal partial work. Drift produces an
explicit refusal; review the conflicting file/crontab rather than replacing it.
If release is interrupted while restoring crontabs, maintenance still makes the
new admission paths refuse; rerun `release --maintenance-confirmed`. Both hold
installation and release flush crontab's actual Debian persistence filesystem
with `sync -f /var/spool/cron/crontabs`. A failed flush retains maintenance;
release never removes it merely because crontab readback matched.

A failed attempt that provably never published may instead be abandoned with
its **original** bundle, while the operator maintenance boundary remains held:

```sh
python3 -m pi.backup_deploy_bootstrap abandon --maintenance-confirmed "$BUNDLE"
```

This refuses any pending/ready/publishing intent, installed recovery closure,
or changed guard predecessor. It restores the recorded crontabs, flushes their
storage, and atomically archives the state directory beside itself as
`vanpi-backup-deploy.abandoned.<id>` (including private evidence). It never
recursively deletes it. The fixed condition drop-ins remain inert because the
maintenance pathname is gone. A corrected, separately reviewed bundle may then
be applied. Once publication may have started, abandonment/rebinding is forbidden:
retain holds and resume the same bundle. An attempt without a durable receipt
may be archived only if it also has no maintenance or publication evidence.

The permanent recovery entry is:

```sh
sudo /bin/bash /usr/local/lib/vanpi-backup-deploy/recover
```

`/etc/cron.d/vanpi-backup-deploy` calls it at minute 17 every hour, independently
of the installed `backup_window.sh`. It returns without changing anything when
no transaction is pending, refuses unsafe journal ownership, and uses the
recorded transaction's own recoverer. It never waits for the legacy fence.

## Normal deployment and interrupted finalization

Only after bootstrap release, run the existing targeted installer from the
reviewed next checkout, for example:

```sh
VANPI_BACKUP_PREVIOUS_REVISION="$PREVIOUS" bash pi/deploy_time_machine_icloud.sh
# Or, for the Pi cloud profile:
VANPI_BACKUP_PREVIOUS_REVISION="$PREVIOUS" bash pi/deploy_icloud_backup.sh
```

The helper checks bootstrap readiness, independent trigger/closure integrity,
and guarded live/staged admission before starting a transaction or stopping
its timers. Time Machine `--check` uses this same read-only readiness check,
including pending/maintenance refusal and staged guards, but never recovers or
publishes; normal deployment repeats it under its fence after recovery. The
verifier is the installed independent bootstrap runtime, not a stage dependency.
Every saved predecessor therefore retains the admission guard.
Existing preserved/defaulted configuration must already have the plan's exact
owner and mode; a mismatch refuses before stopping timers, with no automatic chmod.
Scheduled jobs, direct clone and offsite snapshot startup recheck pending under
their legacy hold; Python cloud admission also checks after verifying its
inherited descriptor. A clone child verifies and preserves either inherited
legacy mode rather than acquiring or downgrading its parent's lock.

Transactions have three durable phases:

- `preparing`: recoverer/closure and timer list durable, no live cohort writes.
- `publishing`: complete private previous tree and manifest durable; recovery
  restores the guarded predecessor, reloads units and restores recorded timers.
- `committed`: published contents and destination directories durable, platform
  checks/reload succeeded. Recovery **does not roll back**: it verifies the new
  cohort and finishes temporary cleanup and timer restoration.

Pending remains through finalization. A timer started before a kill can retry,
but its job refuses pending. Activation requests after commit remain distinct
from timer restoration; a failed activation is reported, not silently replayed.
Files and affected destination directories are flushed before the commit
transition or clearing pending after restoration. Marker replacement/removal
also flushes its parent directory. A flush failure retains recovery intent.
EXIT cleanup rereads the owned marker under privilege and validates its transaction
identity before choosing rollback or finalization. Thus a deferred TERM immediately
after the committed rename cannot override the durable decision with stale shell
state. An unreadable, malformed or foreign decision leaves files and timers alone.

## Rollback and compatibility limits

For an uncommitted interrupted transaction, run the independent `recover` entry
or wait for its hourly invocation. If a restored live file is neither the saved
nor published version, leave pending in place, review that file and retry. Never
remove pending manually. A committed-but-unfinalized transaction only finalizes.

For a later intentional rollback, use a **guard-aware targeted installer and a
guard-aware reviewed target**. The retained manual-tree procedure in AGENTS.md
is not a journaled transaction and is not covered by automatic recovery; use it
only inside separately arranged durable launch holds and operator maintenance,
retaining those holds through complete verification. Never restore an
unbootstrapped predecessor. Preserve
`/usr/local/lib/vanpi-backup-deploy/`, its cron entry, readiness records and cloud
condition drop-ins. The bootstrap guard rejects old payloads through supported
installers, but an old installer cannot be retroactively made to obey it.

Current `pi/sync_scripts.sh` remains the deployment route for **non-backup**
files, including `connectivity_status.py`, flat shared scripts, shell config,
labwc and unrelated units, followed by package/compute updates. Both profiles
of `local_backup_plan` define its exclusions; no second cohort list is maintained.
The receiver independently rejects protected payload/cleanup overlap before any
live write. Its file copier preserves existing directory metadata, including
`backup/`, even if a staged parent is empty. Legacy clone cleanup is not performed
by this non-backup sync.

Only the small inspection closure is uploaded before readiness. The client takes
the existing validated legacy inode **shared**, checks root-private readiness
under privilege, and retains the hold through all unrelated uploads and phases.
The receiver independently takes a shared hold and repeats readiness/scope checks
before writing. Bootstrap and targeted cohort publication need the exclusive
fence, so they cannot slip into the preflight-to-publication gap. Normal jobs may
still take shared admission. Missing locks fail early; this path never creates
or replaces lock inodes. A genuinely unbootstrapped host with its existing legacy
lock remains supported, but broad sync excludes the backup cohort there too.

Maintenance, pending, incomplete or unsafe bootstrap state refuses the entire
sync before unrelated uploads. A retained `bootstrap-publishing` intent alone
is not a failure after verified readiness/release. A stale connectivity helper can
therefore be updated by normal non-backup sync before retrying a targeted backup
installer. An interrupted non-backup sync is not transactional: completed unrelated
writes remain, and retry repeats the checks. Inspection stages are retained in
`/tmp/vanpi-sync-check.*` until ordinary temporary-file cleanup/reboot.

Older/in-flight publishers and arbitrary direct restoration cannot be retroactively
fenced: excluding them remains part of the initial operator maintenance boundary.
Never use them to remove guards or roll back backup-cohort files.

**Constrained upgrades:** readiness deliberately pins exact admission snippets,
including the Python lock CLI tail, clone helper and offsite admission context.
Even a harmless rewrite of these snippets is unsupported by the installed
bootstrap and fails closed. Ordinary cohort upgrades must retain them. Changing
them or the stable bootstrap runtime requires a separately designed, reviewed
maintenance upgrade that preserves existing records and holds; `apply` will not
rebind an already published bootstrap. No generalized migration mechanism is
included in this release. Do not weaken the checks or remove records to force
an upgrade.

## Regression coverage

The Linux tests use real predecessor bytes, real flocks and forced process kills.
They cover bootstrap interruptions/reruns, each first cohort publication boundary,
recovery killed after restoring the old window, post-check/pre-flock admission,
root-private journals written by a different installer UID, committed timer
finalization, injected flush failures and direct/inherited clone admission.
Scheduler adapters operate on fixture files only; live supervisor readback and
actual power-loss behavior remain deployment-validation responsibilities.
