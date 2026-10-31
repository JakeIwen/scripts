# Metered connections

## Ownership and safety

`shared/metered/` owns Python 3.9+ records, pure decisions, the consumer registry
and configuration transactions. `pi/apps/metered/` is the Python 3.11 Pi adapter.
`pi/deploy_python.py` owns its immutable package and `vanpi-metered.service`.
The flat `/home/pi/scripts/vanpi-metered` launcher is the stable SSH/shell/cloud
boundary; it fixes the Pi home and package import root. No HTTP listener is added.

The independent service schedules every 10 seconds. Observation, API requests and
graceful-stop deadlines add to that interval. This is not a hard byte or time
cap: a failover and recovery between samples is invisible, and router connection
tracking may retain old flows. Observed route/association changes stop and resume
owned peer sessions before allowing traffic again; failure to confirm a reset
falls back to process termination. Live TCP/uTP failover validation is a release
check. A hard cap requires router enforcement, not a faster dashboard poll.

Unknown routing, association or freshness is closed for cost gating. A temporary
cloud approval bypasses only cost: source/VPN, IPv6, rule-name, association and
freshness failures remain denied. LAN Time Machine and local backups are not gated.
The dashboard's existing trusted-LAN and origin/header boundary is unchanged.

## Persistent records

Definitions and validation are in `shared/metered/records.py`.

- Shared config: `/home/pi/.config/vanpi/metered.json`. First-run defaults come
  only from `config.first_run_config`; rules read the record. Only the Pi writes
  this authoritative copy. SSID comparisons preserve exact case and whitespace.
- Pi permissions: `/home/pi/.config/vanpi/metered-policy.json`. Permissions never
  synchronize to another host. Timed approvals and forced-unmetered mode expire;
  all durations are at most 24 hours. Episode approvals end on an observed return
  to unmetered. Unknown preserves the current metered episode.
- Pi state: `/home/pi/.local/state/vanpi-metered/state.json`. `config_revision`
  and `policy_revision` invalidate cached admission after edits. `route_signature`
  records the last successfully enforced torrent-route identity, not merely the
  most recent read. Decision-only CLI calls cannot consume a pending route reset.
  An old or future-dated state is not permission to run. External consumers must
  also require a numeric, future `valid_until`; this deadline never outlives the
  current proof or the next mode/approval expiry. `permitted` alone is not a lease.
- Last Mac report: `mac-report.json` beside the Pi state. The status response uses
  `null` until one arrives. Reports are telemetry only; stale/future/older reports
  cannot replace a recent one or control Pi permissions.
- Torrent fallback ownership: `torrent-fallback.json` beside the Pi state. The
  adapter only requests process recovery when metering stopped that process,
  and delegates startup to the existing disk/torrent policy service.

Stores use file locks, compare-and-swap revisions, private atomic publication and
file/directory synchronization. Receipt writes and config changes are one
transaction. Only `add_ssid` may replay offline. Reused operation ids with different
content are rejected; remembered ids are acknowledged without reapplying, even if
that SSID was subsequently removed. There are at most 4096 receipts and 256 SSIDs;
capacity exhaustion refuses new edits rather than forgetting old receipts.

The registry distinguishes desired permission from actual enforcement. Phase-two
consumers remain `unprotected` until their integrations land. `advisory` never
claims a process was stopped. Torrent `stopped_manually` means stopped outside
metering ownership, including storage/ignition policy—not necessarily a human
pressing Pause. A Low Data Mode adapter constrains the interface
when the protective consumer is *not* permitted; `permits` is a data permission,
not an instruction to turn Low Data Mode on. Python `rules.permits(...)` returns
`ConsumerState`; callers use its `.permitted` boolean, not record truthiness.

## Public commands

Run the installed launcher, not an internal Python file:

```sh
/home/pi/scripts/vanpi-metered status --json
/home/pi/scripts/vanpi-metered config --json
/home/pi/scripts/vanpi-metered is-metered -v
/home/pi/scripts/vanpi-metered allows pi.torrents -v
/home/pi/scripts/vanpi-metered ssid add 'Exact SSID'
/home/pi/scripts/vanpi-metered ssid remove 'Exact SSID' --revision 2
/home/pi/scripts/vanpi-metered mode metered --for 1h
/home/pi/scripts/vanpi-metered mode unmetered --for 1h
/home/pi/scripts/vanpi-metered mode auto
/home/pi/scripts/vanpi-metered approve pi.torrents 1h
/home/pi/scripts/vanpi-metered approve pi.icloud_backup episode
/home/pi/scripts/vanpi-metered revoke pi.torrents
/home/pi/scripts/vanpi-metered exempt pi.torrents off
```

`is-metered` exits 0 for metered/unknown, 1 for verified unmetered. `allows` exits
0 only for permission, and 1 for denied/invalid/missing state. Revision conflicts
on mutations exit 75; other invalid mutations exit 2. Status, config, mutations,
`sync` and `admit` print JSON. `status` is read-only and performs no network probes.
Admission refreshes stale evidence. `run` is the supervised loop; `tick` performs
real enforcement once and must not be used as a read-only check.

Run-time consumers: `pi.codex_update`, `pi.claude_update`, `mac.codex_update`,
`mac.claude_update` (independent weekly adapters in `shared/sh/codex_update.py`)
and `pi.ytdlp_update` (weekly controller cron) require host-local `allows` permission;
missing, failing or timed-out permission commands defer. Every scheduled Claude
check first atomically merges only `env.DISABLE_AUTOUPDATER="1"` into its existing
regular `~/.claude/settings.json`, even when denied or not due; invalid JSON is
left untouched and that adapter fails. CLI/state paths and six-hour cron entries
remain stable; flat legacy Codex state retains its cadence. Marketplace updates
remain unmanaged: `claude plugin marketplace update [name]` supports on-demand
refresh, but the inspected CLI exposes no auto-update toggle. The supported
`extraKnownMarketplaces.<name>.autoUpdate` setting and `known_marketplaces.json`
flags are intentionally unchanged rather than silently broadening the settings
merge beyond its owned environment key.

`metered_shell.bash` provides `isMetered`, `meteredAllows`, and `vanpi-metered`.
`startor [1h|4h|episode]` grants a temporary Pi torrent approval (default 1h);
`startorx` revokes it. Neither changes the global torrent/disk switches.

qBittorrent holds tag active torrents before stopping them. Only tagged torrents
resume. Removing `metered-hold` from a stopped torrent makes its stop manual;
merely stopping an already held torrent cannot distinguish user ownership.
The adapter supports qBittorrent 4 pause/resume and 5 stop/start endpoints.

## JSON boundaries

SSH sync runs `/home/pi/scripts/vanpi-metered sync`, JSON on stdin/stdout:

```json
{"ops":[{"op_id":"unique-operation-id","op":"add_ssid","ssid":"Exact SSID"}],"mac_report":null}
```

The response is `SyncResponse`: `{config, pi_state, applied}`. Use bounded,
noninteractive SSH with the configured `vanpi` alias; no second HTTP sync transport
or approval replay protocol exists. A lost response is retried with the same ids.
The Mac keeps the Pi's revision unchanged in its cache and overlays unacknowledged
restricting additions locally; it must not invent a new authoritative revision.

`GET /api/metered` returns `{ok:true, ...MeteredStatus}`: shared config, Pi policy,
Pi state, registry and the optional last Mac report.

`POST /api/metered/ssids` takes `SSIDChange`:

```json
{"op":"add","ssid":"Exact SSID","revision":0}
```

`op` is `add` or `remove`. `POST /api/metered/policy` takes `PolicyChange`:

```json
{"op":"approve","revision":0,"consumer":"pi.torrents","value":"until","seconds":3600}
```

Other operations: `approve` with `value:"episode"` and no seconds; `revoke` with
consumer; `mode` with `value:"auto"|"metered"|"unmetered"` and optional seconds
(required for unmetered); `exempt` with consumer and boolean `enabled`. All
mutations require the current revision. Foreign-host consumers are rejected.
Browser requests retain `X-Van-Dashboard: 1` and same-origin requirements. Invalid
input returns 400, stale revisions 409. CLI equivalents `edit-ssids` and
`edit-policy` accept the same records on stdin.

Cloud workers submit their freshly collected legacy probe envelope to
`vanpi-metered admit pi.icloud_backup` or `admit pi.time_machine_cloud`, receiving
`{allowed, reason}`. The short-lived package reader avoids pinning a package
release for a potentially multi-day flat cloud worker. The guarded workers still
pause transfers during probes and restart on a changed routing signature.

## Mac controller

`macbook/metered/mac-metered` is the stable checkout launcher (system Python
3.9). It mirrors status/config/is-metered/allows/ssid/mode/approve/revoke/exempt
and adds `sync`, `tick`, and `low-data-mode on|off|restore`. Status and admission
are read-only: no Wi-Fi probe, SSH, tick or sudo. Missing, corrupt, expired,
future-dated or revision-mismatched state denies `allows` (exit 1) and counts
as metered (exit 0). Mutation conflicts exit 75; invalid mutations exit 2.
`status` and successful `sync` return `{config, policy, state, registry,
pi_state, pending, sync, fresh, config_cached, low_data_mode}`; `config` is the
local overlaid SharedConfig and `tick` returns HostState. The stored config file
remains the exact authoritative Pi record. `ssid add` returns
`{queued: AddSSID, pending_sync: true}`; policy mutations return HostPolicy.

`com.jacobr.metered` is a GUI LaunchAgent (Location needs a login session),
RunAtLoad plus every 60 seconds. From the checkout, install with
`zsh macbook/metered/install.zsh install`; use `prepare`, `status`, `stop`,
`start` or `uninstall` with the same script. No job is loaded by tests.
State, authoritative config cache, Pi telemetry, sync metadata and pending
operations live under `~/.local/state/metered/`; Mac permissions alone live
in `~/.config/jacobr/metered.json`. Queueing an SSID add immediately invalidates
cached admission; the next tick overlays it, without changing the Pi revision.
Remove requires `--revision` and a live Pi acknowledgement after replay.
Only confirmed gateway IP **and MAC** allow minute syncs; elsewhere attempts
are at most once per 15 minutes (explicit `sync` bypasses this rate limit).

Mac-local observations have a 75-second lease, bounded by mode/approval expiry.
An inherited Pi verdict additionally expires at the Pi's `valid_until`
and evidence deadline (30 seconds). Thus a 60-second Mac timer intentionally
has closed-admission gaps on the van LAN; cached Pi evidence is never extended
to hide the gap. `sync` refreshes decisions but never changes interface flags;
`tick` is the enforcing command. The Mac Panel shows Pi state read-only with age.

Low Data Mode defaults **OFF** (`shared/settings/metered-mac.json`, separate
from HostPolicy). Run `zsh macbook/metered/install_low_data_mode_sudoers.zsh`
yourself; it verifies the LAN, installs narrowly scoped sudoers, tests the
Pi SMB Time Machine destination and restores the prior flag. A pass requires
exit 0, idle Time Machine, a **new** latest completed backup and CONSTRAINED
still set before restoration. Only then enable in the panel or run
`macbook/metered/mac-metered low-data-mode on --confirm`.
The adapter reads `ifconfig -v` (CONSTRAINED is an **extended** flag), never
sets expensive, and relinquishes ownership on identity change or a sampling
gap. The bit cannot identify an intervening native/user write: automatic
clearing therefore remains disabled. After granting Low Data Mode permission,
`low-data-mode restore --confirm` clears only a recorded flag on the same
continuously observed network. Native/unowned flags stay untouched. Disabling
control does not clear a flag. iCloud remains advisory, not independently held.

## Deployment sequence (not performed by the implementation task)

First integrate the phase-two frontend removal of the old Starlink-only switch.
The old storage status wire shape temporarily returns that flag as **false**;
it cannot grant an exemption. Stored `policy.json` is version 2 without the field.
The third compact `policyctl read` column stays zero for existing backup readers.

1. Run the whole suite from the checkout:
   `.local/test-venv/bin/python run_tests.py`. Review live hashes/units and retain
   private previous copies of the flat cohort and `policy.json`. Resolve any
   live/check-out drift before installing; preserve independent cloud pauses.
2. Preview and install the new package-owned unit, initially inactive:
   `python3 pi/deploy_python.py --dry-run --activate --service vanpi-metered.service`,
   then the same command without `--dry-run`. This supports genuine unit absence;
   it refuses foreign/vendor units and overrides. It does not fabricate a flat
   predecessor or enable an inactive service.
3. Stage these non-backup files over `vanpi.lan`: `pi/scripts/vanpi-metered`,
   `pi/scripts/metered_shell.bash`, `pi/scripts/connectivity_status.py`,
   `pi/scripts/policyctl`, and `pi/scripts/internet_switches.sh`.
   Preserve each live original before installing. Publish the CLI and connectivity
   collector first (mode 755 for executables; 644 for the sourced shell helper).
4. Use the existing scoped backup deployers, in their documented guarded workflow:
   `bash pi/deploy_icloud_backup.sh`, then `bash pi/deploy_time_machine_icloud.sh`.
   Their prerequisite checks must see the new connectivity collector and package
   CLI. Do not copy individual cloud workers around their owner deployers.
5. Enable and start the independent reconciler:
   `ssh vanpi.lan 'sudo systemctl enable --now vanpi-metered.service'`.
   Verify `systemctl is-active`, fresh state and the observed torrent hold before
   removing the old power-based gate. An unknown state must be held, not permitted.
6. Install the staged `policyctl` and `internet_switches.sh` together, retaining
   rollback copies. Run `/home/pi/scripts/policyctl --no-reconcile migrate`, then
   `/home/pi/scripts/policyctl reconcile` over SSH. Migration preserves disk and
   torrent switches, removes the old indefinite exemption, and never grants a new
   approval. Update only the reviewed `.bashrc` wrapper lines after comparing its
   live version; do not overwrite unrelated `nfn` additions.
7. Update the dashboard backend:
   `python3 pi/deploy_python.py --update --service van-dashboard.service`.
   Check `http://vanpi.lan:8788/api/metered`, the service journal, registry/state
   decoding and expired-approval behavior. Neither the backend nor the reconciler
   requires a frontend build to start, but the old switch must not be presented as
   a functioning permission control.

For a later shared-library change, select **both** `vanpi-metered.service` and
`van-dashboard.service` with the package updater. Both dependency digests include
shared metering code and JSON assets. The generic sync excludes the new unit;
activation must precede routine all-service updates.

Rollback must restore the flat cloud/admission/policy cohort coherently with the
matching package. Restore the saved policy file only after checking for newer
user edits; never overwrite new approvals/config as incidental code rollback.
Retain a safe torrent hold while replacing enforcement, and preserve all backup
resource locks. A rollback that only stops the reconciler leaves no cost guard.

## Verification

Offline tests cover rules, clock/freshness handling, revisions, replay receipts,
independent reconciliation and expiry, real loopback qBittorrent protocol effects,
manual holds, graceful-stop fallback, CSRF boundaries, cloud separation, shell
admission, new-unit activation/retry and unpacked package imports. Use the whole
repository runner; tests perform no live router or torrent writes.

Live checks still required after an explicitly authorized deployment: dashboard
shutdown/restart independence, hotspot-to-metered failover, return with existing
TCP/uTP peers, manual tagged/untagged holds, process/API failure, approval expiry,
and preservation of ordinary disk/ignition/manual cloud holds. Mac Location,
Low Data Mode and LAN Time Machine verification belong to the Mac phase.
