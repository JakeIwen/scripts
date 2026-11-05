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

`apply --maintenance-confirmed` records durable maintenance first. It saves both
root/pi crontabs before changing either, comments only recognized backup-start
entries, and refuses unknown `/backup/` cron commands rather than guessing.
Watchdog and disk/ignition jobs remain alone. Four cloud service drop-ins add
`ConditionPathExists=!/var/lib/vanpi-backup-deploy/maintenance`; timers need not be
stopped or masked, and a reboot does not remove the condition. Existing active
workers are not killed. Before guard publication, the tool reloads systemd and
uses its typed `busctl --json=short` Conditions property to require the effective,
non-triggering negated maintenance condition on each cloud service. Merely finding
its own drop-in file or a successful reload is insufficient; missing units or
an overriding condition reset refuse before guard edits.

The tool then takes the existing legacy lock non-blocking, installs its
independent recovery closure and cron entry, and applies minimal guard edits to
an explicitly selected predecessor. All live hashes must match that reviewed
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

The default target is `pi@vanpi.lan`; there is no implicit Tailscale fallback.
`verify` is read-only and permits the expected held maintenance state. The remote
apply stage is temporary, but the input bundle and crontab originals are copied
and flushed under `/var/lib/vanpi-backup-deploy/` before any guard publication.
The original local bundle is still needed to rerun the normal apply command.

If apply is interrupted, repeat those three commands with the **same** bundle.
Do not delete `maintenance`, manually uncomment held jobs, remove the condition
drop-ins or use a different bundle to conceal partial work. Drift produces an
explicit refusal; review the conflicting file/crontab rather than replacing it.
If release is interrupted while restoring crontabs, maintenance still makes the
new admission paths refuse; rerun `release --maintenance-confirmed`.

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
its timers. Every saved predecessor therefore retains the admission guard.
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

Older installers, broad `pi/sync_scripts.sh` publication and arbitrary direct
file restoration are unsupported once this bootstrap is installed. They could
remove admission checks without leaving a transaction. Do not use them to
roll back or publish backup-cohort files. Updating the bootstrap itself requires
another explicitly reviewed maintenance operation, not ordinary cohort rollback.

## Regression coverage

The Linux tests use real predecessor bytes, real flocks and forced process kills.
They cover bootstrap interruptions/reruns, each first cohort publication boundary,
recovery killed after restoring the old window, post-check/pre-flock admission,
root-private journals written by a different installer UID, committed timer
finalization, injected flush failures and direct/inherited clone admission.
Scheduler adapters operate on fixture files only; live supervisor readback and
actual power-loss behavior remain deployment-validation responsibilities.
