# NanoStation Wi-Fi selection

The device stores full airOS configurations for saved networks under
`/etc/persistent/profiles`. Those files contain credentials, are ignored by Git,
and are never included in a normal code deployment.

The antenna's live admin username/password take precedence over saved network
templates. Before applying a profile or provisioning a new one, the manager
copies only those login fields from the current device configuration, without
logging them or passing them in command arguments. Missing or ambiguous live
credentials fail closed. Upstream Wi-Fi passwords remain network-specific.

## Manager commands

On the NanoStation:

```sh
/etc/persistent/scripts/wifi_manager.sh status
/etc/persistent/scripts/wifi_manager.sh connect 'profile name'
/etc/persistent/scripts/wifi_manager.sh pause
/etc/persistent/scripts/wifi_manager.sh resume
```

The van dashboard uses these additional fixed manager entry points:

```sh
/etc/persistent/scripts/wifi_manager.sh dashboard-status
/etc/persistent/scripts/wifi_manager.sh dashboard-scan
/etc/persistent/scripts/wifi_manager.sh manual-connect-stdin
/etc/persistent/scripts/wifi_manager.sh provision-stdin
/etc/persistent/scripts/wifi_manager.sh update-profile-stdin
/etc/persistent/scripts/wifi_manager.sh forget-stdin
/etc/persistent/scripts/wifi_manager.sh starlink-off
```

The first two emit credential-free, hex-encoded records for
`pi/scripts/ubnt_wifi.py`. The `*-stdin` commands read their selection or provisioning
request from standard input so Wi-Fi passwords never appear in SSH arguments,
process listings, command output, or manager logs. The dashboard lists saved
profiles independently from scan results and can update a WPA password, Lock to
AP address, output power, data-rate module, and maximum TX rate. Profile edits
create a recoverable copy under the profile directory's `.disabled` folder and
are persisted with `cfgmtd`. They can either wait for the next connection or be
applied immediately with an explicit reconnect. New profiles support
WPA/WPA2 Personal and open networks; WEP is reported to the UI as unsupported.
After association, provisioning deliberately runs the same `save-current` and
`cfgmtd` persistence path as an explicit profile save.

Manual connections never create an indefinite pause. They retain the existing
120-second association/reload protection. An associated network without Internet
gets up to ten minutes from the original request for captive-portal login
(`UBNT_PORTAL_GRACE_SECONDS`); polling never renews that deadline. Protection
ends early when Internet works, or when the link is lost after the initial
two-minute window. The dashboard shows the remaining upper bound and allows
Resume automatic selection to end it early. `pause` remains an explicit,
indefinite maintenance override for deployment/canary work; ordinary connect,
provision, and apply-profile actions do not create it.

`forget-stdin` reads one saved profile name. It moves that profile into
`.disabled` for recovery and persists the removal. Forgetting the active SSID
also applies a disconnected temporary radio configuration while preserving
current Ethernet settings, clears the manual hold, and scans for a different
saved uplink. It does not load the historical `reset` profile (which actually
targets an unrelated network). Internal profiles cannot be forgotten.

Dashboard network changes retain their lock and complete credential saving
even if the initiating SSH connection hangs up during an airOS reload.
Flash-save completion and failures are logged explicitly, and persistent SSH
keys are restored after both reloads and profile writes.

Dashboard Starlink power-off queues an immediate `starlink-off` operation. It
releases denlink if configured or connected, clears that connection's temporary
protection, and scans for the best other saved network without waiting for cron
or repeated Internet failures. The denlink profile stays saved. A two-minute
cooldown prevents automatic reselection while the AP powers down; an explicit
power-on connection bypasses it. Other Wi-Fi connections are untouched, and an
explicit maintenance pause is retained (disconnect only, no automatic roam).
If another antenna operation is in progress, the latest power intent runs after
that operation reaches a safe boundary; rapid off/on changes replace queued
intent. The CLI reconciles a dropped reload connection without replaying the
mutation. A deliberate disconnected state does not start another association
grace period, so subsequent automatic scans can continue if no alternative is
currently visible.

`connect` first uses the frequency of the strongest matching SSID/security/AP
from a completed scan at most five minutes old. It gives that channel 15 seconds
for association, then reloads with all 11 standard US 2.4 GHz center frequencies
(2412 through 2462 MHz in 5 MHz steps) and waits up to 60 seconds if the targeted
attempt fails. Missing, stale, or nonstandard-frequency hints go directly to the
full allowlist. Channel hints never overwrite saved profiles. `UBNT_SCAN_MAX_AGE_SECONDS`,
`UBNT_ASSOCIATE_FAST_SECONDS`, and `UBNT_ASSOCIATE_FALLBACK_SECONDS` tune these bounds.

Automatic and dashboard site surveys aggregate three passes because individual
airOS scans can omit visible networks. Before a survey, the manager replaces any
live channel pin or incomplete/unrestricted scan list with the standard allowlist
using one airOS reload. This prevents a successful targeted connection from
limiting later discovery, and excludes proprietary 2 MHz-offset Channel Shifting
frequencies. `UBNT_SCAN_PASSES` and `UBNT_SCAN_SETTLE_SECONDS` tune surveys. Saved
profile copies always contain the full allowlist; saving never changes the live
configuration behind the radio's back. The manager records the configuration
digest after changes it applies itself, keeping them distinct from native GUI edits.

The Pi CLI allows 60 seconds for status and scan requests. Dashboard process
deadlines include headroom for those reads and post-reload reconciliation;
a slow preliminary status read must not prevent a connection attempt after 15 seconds.

A user-requested switch owns one 120-second protection window beginning before
its reload; recovery attempts do not extend that deadline. If association times
out, the same command runs a standard-frequency multi-pass scan and recovers to the
best visible saved profile. Raw airOS reload output is captured in a mode-600
temporary file and deleted, preventing configuration diffs and credentials
from being printed to the terminal or logs.

`save-current PROFILE` is the explicit profile-write operation. Native airOS
GUI changes also enter a separate ten-minute stabilization window. The manager
does not scan or fall back to an older SSID during that window. Once the GUI's
target SSID is associated and has an IPv4 address, default route, and working
Internet check, its current configuration is automatically saved under the
SSID name. A single-frequency setting introduced by the GUI is cleared and the
connection revalidated before that save, keeping both later site surveys and
the saved profile on the standard 11-frequency allowlist. This restores the historical
GUI-to-profile behavior without allowing automatic roaming operations to
overwrite profiles. If a profile
already exists, its previous version is copied into the profile directory's
`.disabled` folder first. `UBNT_GUI_GRACE_SECONDS` can tune the bounded GUI
window; an explicit maintenance pause continues to protect the transition until
automatic selection is resumed. `disable PROFILE` moves a profile into
`.disabled` instead of deleting it.

## Automatic selection

Cron calls `wifi_manager.sh auto` once per minute. An atomic directory lock
prevents overlap. Native airOS GUI changes receive the ten-minute stabilization
window described above. Other manual/config transitions retain their
120-second protection window, and an established connection must fail three
checks before selection changes.

Unlisted profiles have priority 100. `denlink` normally has priority 10 so any
other saved network wins when available. If `config/prefer_denlink` exists,
`denlink` has priority 1000. Overrides use `priority|profile filename` lines in
`persistent/config/wifi-priority`.

Runtime locks, scans, cooldowns, and logs are kept in `/tmp` or `/var/log`, not
under `/etc/persistent`.

airOS regenerates `/etc/dropbear/authorized_keys` during a wireless soft
reload. The boot hook and Wi-Fi manager therefore reinstall the keys listed in
`persistent/config/raspi_rsa_id.pub` after boot and after every reload.

The main runtime log is capped at 256 KiB and retains its most recent 1,000
lines when rotated. Cron error logs use the same limits through an hourly
rotation job. Healthy-link heartbeats are written only when the SSID changes or
once per hour; switches, failures, cooldowns, and transitions are always logged.
These defaults can be tuned with `UBNT_MAX_LOG_BYTES`, `UBNT_LOG_KEEP_LINES`,
and `UBNT_HEALTHY_LOG_INTERVAL`.

## Backup and deployment

Always back up first:

```sh
./backup_profiles.sh ubnt@192.168.8.20 --sync-working
```

Backups are permissions-restricted under the ignored `private-backups/`
directory. Deployment also runs this backup automatically.

Deployment modes are deliberately separate:

```sh
./scp_to_device.sh --stage-only
./scp_to_device.sh --install-paused
./scp_to_device.sh --activate
```

- `--stage-only` uploads to `/tmp`, validates with the device's BusyBox tools,
  then removes the staging directory without changing live files.
- `--install-paused` installs and persists code, stops cron, and leaves automatic
  selection paused for a manual canary test. It also refreshes the login
  `profile` shim without activating automatic selection. airOS sources that shim
  from `/etc/profile`, and the shim loads `config/.profile`.
- `--activate` installs and starts the new cron configuration.

Every install records a code-only rollback under
`/etc/persistent/rollback/code-TIMESTAMP`. The new rollback plus the newest
previous rollback are retained by default (two total); oldest timestamp-named
`code-*` directories are pruned first. The rollback contains code, not another
copy of the rollback tree. `--keep-rollbacks N` changes the total (positive
integer, minimum one). `profile-*`, `auth-*`, profiles, and unrelated persistent
files are never pruned. Symlinked or unrecognized code rollback names fail
closed for manual review.

To archive the exact rollbacks that will be pruned **before deleting them**:

```sh
./scp_to_device.sh --activate --keep-rollbacks 2 --backup-pruned
```

This optional archive and its MD5 receipt are stored in a private, ignored
`private-backups/pruned-code-TIMESTAMP.XXXXXX/` directory. Transfer, checksum,
or archive-integrity failure aborts before any live changes. Without
`--backup-pruned`, old code rollbacks are pruned without this extra Mac copy;
the usual automatic profile backup still runs in either case.

Before creating a live rollback, deleting anything, stopping cron, or replacing
code, `deploy_remote.sh` builds a complete prospective tree in `/tmp`: existing
persistent files, the new code rollback, the retained older rollbacks, and the
new code. It compresses that tree plus a copy of `/tmp/system.cfg` and measures
it with `wc -c`. The maximum is **112 KiB (114,688 bytes)**. The observed cfg
partition is 256 KiB and contains active and backup slots; 128 KiB per slot
minus 16 KiB headroom gives this conservative ceiling. This is a safety budget,
not a claim to know the firmware's exact headers, slot layout, or serializer.
`--max-bytes N` may lower the ceiling, never raise it. Oversize or archive errors
leave live files and cron untouched. Preserved profile/auth history counts
toward the limit; lower code retention or review/archive that history manually
rather than bypassing the limit.

Installation takes the Wi-Fi manager's existing runtime lock without killing
an active manager. It then rechecks the complete persistent snapshot and
`system.cfg`; concurrent changes during preparation or the Mac backup cause a
refusal/retry. Avoid native airOS GUI edits and other flash-writing tools during
deployment: they do not honor the manager lock. The preview never replaces the
whole live tree—only explicit code files, the new rollback, and the listed old
code rollbacks change.

After `cfgmtd -w -p /etc/`, deployment reads active slot 1 into a fresh directory
using `cfgmtd -r -t 1 -p /tmp/<transaction>/readback/ -f /tmp/<transaction>/readback/system.cfg`.
It checks the staged `wifi_manager.sh` MD5 against the extracted deployed file,
not RAM or a matching rollback copy. Only the exact `persistent/scripts/` or
`scripts/` extraction layout is accepted; missing, ambiguous, or mismatched
readback fails the deploy. These layouts are fake-test contracts, not a claim
of live firmware validation.

Any ordinary error or HUP/INT/TERM after the cron-stop attempt triggers
`rc.postsysinit`, with `crond` fallback and a process check, and exits nonzero.
This includes a child `cfgmtd` segfault and failed readback. Cron recovery is
reported; it is **not** an automatic rollback of partially installed code or
flash. Power loss/SIGKILL cannot be trapped. Successful `--install-paused`
still leaves cron stopped; `--activate` still preserves an existing explicit
maintenance pause. Once installation starts, remote cleanup owns the staging
directory so an SSH disconnect cannot delete files the worker still needs.

Restore a retained rollback with:

```sh
./rollback_device.sh code-YYYYMMDDTHHMMSSZ
```

The legacy rollback helper is unchanged and does not provide the new deploy
preflight/readback/recovery safeguards; use it only in supervised maintenance.
Rollback and deployment never replace or delete the live profiles directory.

The editable push/pull copies above remain the normal operational workflow.
Separately, vanpi pulls the device's complete `/etc/persistent` tree immediately
before its encrypted Borg backup to `bigboi`. That snapshot is for catastrophic
recovery and never syncs changes back into this checkout. See
[`UBNT_BACKUP.md`](../pi/scripts/backup/UBNT_BACKUP.md).

## Tests

```sh
for test in tests/test_*.sh; do /bin/dash "$test" || exit; done
bash -n scp_to_device.sh
dash -n deploy_remote.sh
```

Deployment tests use fake SSH/SCP and `cfgmtd` with isolated filesystem/process
fixtures; they never contact an antenna. Python 3 is required for that harness.
