# Repository guidance

## Private local context

If `AGENTS.private.md` exists, read it before work involving live devices,
networking, storage, backups, or vehicle tooling. It contains private,
potentially stale operational context and is intentionally excluded from Git.
Use it only to guide the task at hand: do not quote it, summarize it into tracked
files, print it in logs, or include its details in commits, issues, or reports.

Never store passwords, tokens, private keys, Wi-Fi credentials, or secret service
URLs in an agent-context file. Use the existing secret-management mechanism.

## Scope and source quality

This repository contains personal scripts and configuration for Raspberry Pi
services, OpenWrt and wireless equipment, media services, backups, desktop
utilities, home automation, and vehicle-related tooling.

Treat repository configuration and historical notes as hints, not proof of live
state. Before changing a live device, inspect its current configuration and
compare it with this checkout. Deployed files may differ from the repository.

## Repository map

- `pi/`: Raspberry Pi apps, backup, disk, service, deployment, and setup tooling;
  `pi/README.md` is the documentation index.
- `vanrouter/`: reviewed OpenWrt persistence and backup-export artifacts.
- `ubnt/`: directional wireless-device profiles and uplink scripts.
  `ubnt/persistent/scripts/wifi_manager.sh` owns initialization and command
  dispatch; its flat `wifi_manager_*.sh` siblings contain function definitions:
  `runtime` (logging, locks, failures/cooldowns), `profiles` (inspection, admin
  login, editing/persistence), `scanning` (surveys, channels, automatic selection),
  `connect` (link checks, reloads/recovery), `transitions` (GUI/manual holds),
  `provision` (new networks), `status` (status/dashboard output), and `starlink`
  (power-off roaming). Ship the entrypoint and every sibling together.
- `macbook/`: macOS shell utilities, AppleScripts, and BetterTouchTool helpers.
- `shared/`: code imported or deployed on more than one host.
- Network storage deployment test filesystem fixtures live in
  `pi/tests/network/_network_storage_support.py`.
- Time Machine cloud holds: `pi/scripts/backup/time_machine_icloud_control.py`
  owns `pause.json` independently of worker state; the minute resume timer,
  worker and Mac heartbeat all honor it. Dashboard mutations use
  `van_dashboard_cloud_controls.py`. Samba mount reconciliation clears only
  empty shutdown gates; nonempty capture gates are released by their owner.
- Deal Watch cookie renewal: `pi/scripts/price_check/search_watch/browser_refresh.py`
  owns the durable request queue and request contract; `refresh_install.py`
  validates submitted headers on the Pi before replacement. The bridge is
  `pi/scripts/price_check/ebay_refresh.py`; the Mac polling worker and managed
  clean-browser client are `macbook/scripts/deal_watch_refresh.py` and
  `macbook/scripts/ebay_browser_headers.mjs`. Browser session/verification code is
  `macbook/scripts/deal_watch_browser.mjs`; the shared header-name contract is
  `pi/scripts/price_check/search_watch/browser_headers.json`. Preserve full captured
  Chromium headers without adding legacy Firefox defaults. See
  `pi/docs/dashboard/PRICE_CHECK.md`.

Some ignored secret directories, wireless profiles, and device configurations
may contain credentials or private network data. Never print or commit secrets,
and preserve unrelated local or deployed changes.

## Vanpi connections

When connected to the van's local network, prefer `vanpi.lan` for SSH and other
connections to the Raspberry Pi. The bare hostname `vanpi` resolves through
Tailscale and should generally be used only as a fallback when `vanpi.lan` is
unavailable, such as when the client is not connected to the local network.
Use `vanpi.lan` in new connection defaults and command examples, including SSH,
HTTP, and media URLs; do not introduce bare `vanpi` or `vanpi.local` as a primary
connection target. Keep device identities, service names, and filesystem paths
unchanged.

## UBNT manual-selection protection

Ordinary antenna connections must not create indefinite automatic-selection
pauses. Keep the 120-second connection grace and bounded captive-portal hold
(default ten minutes from the original request, ending early when online or
the link is lost after the connection grace). The explicit manager `pause`
command is reserved for maintenance/canary work and still requires `resume`.
See `ubnt/README.md`; do not conflate that maintenance override with dashboard
network selections when changing roaming behavior.

Dashboard Starlink power-off releases denlink and scans for another saved
network immediately, keeping the denlink profile and leaving unrelated Wi-Fi
connections alone. Serialize power intents behind active antenna mutations;
never kill a radio reload to accelerate power-off. Preserve explicit maintenance
pauses and the bounded cooldown that prevents reselecting the shutting-down AP.

Channel-aware connections may temporarily pin the live radio to a fresh scan's
matching AP frequency. Keep the standard-channel fallback and full-survey
normalization. Save the full allowlist into profile copies without changing the
live configuration to conceal a pin that is still applied to the radio.

The live UBNT admin login belongs to the device, not a saved Wi-Fi profile.
Preserve it when applying old profiles or provisioning from templates. Never
log credential fields or pass hashes in process arguments. Normalize saved
login fields only from an explicitly selected trusted source, with a private
backup and verification that upstream Wi-Fi credentials remain unchanged.

## Vanpi CAN/UDS workspace

The primary CAN-bus research and diagnostic toolkit is a separate Git checkout
on vanpi at `/home/pi/dev/obd-things/`; it is not mirrored by this repository.
Before CAN/UDS work, inspect that live checkout and its Git status, then read its
root `README.md`, `CLAUDE.md`, and `docs/bus-map.md`. It may contain active,
uncommitted investigations, so preserve its changes independently from this
repository.

Its main contents are:

- `bringup.sh`: PCAN/SocketCAN setup for C-CAN (500 kbit/s) and B-CAN
  (125 kbit/s), passive/listen-only by default; transmit mode is explicit.
- `lib/`: reusable CAN interface/bus detection and wake helpers, ISO-TP/UDS
  request handling, and the ECU/module-address registry.
- `tools/`: generic UDS requests and DID/routine discovery, CAN field finding,
  signal correlation, decoding, and capture helpers.
- `live_data/`: reusable terminal live-data viewer infrastructure.
- `projects/`: battery-voltage, ECU-mapping, radar/ACC, and TPMS investigations,
  with project-specific READMEs, scripts, findings, and some systemd units.
- `tmp/`: ignored, transient captures, sweeps, logs, locks, and other runtime
  output. Durable findings are deliberately promoted into the relevant
  `projects/<name>/findings/` directory.

Some helpers can reconfigure `can0`, wake a bus, issue diagnostic requests, or
perform safety-critical radar actuation. Their presence is not authorization to
run them: follow the live repo's guidance, coordinate with any service owning
the interface, and retain the CAN-transmission safeguards below.

## Live infrastructure safety

- Prefer read-only inspection before changing routing, wireless, firewall,
  storage, mounts, backups, cron, or services.
- Establish and verify a recovery path before a remote Wi-Fi or routing change
  that could disconnect the current client.
- Resolve device names, filesystem labels, mount state, and command paths
  explicitly. Never assume `/dev/sdX` still identifies the same USB device.
- Some identical USB readers do not have unique serials. Backup and clone targets
  must use verified filesystem labels or carefully checked physical paths, not an
  ambiguous `/dev/disk/by-id` entry.
- Check the exit status of discovery commands. An error or empty result is not
  evidence that a disk, filesystem, or mount is absent.
- Non-interactive shells can have a reduced `PATH`; use explicit trusted command
  paths where a safety check depends on a system utility.
- Before deletion or recursion, resolve and validate the exact target and mount
  state immediately beforehand. Fail closed when validation is incomplete.
- Prefer `rmdir` for an expected-empty mount directory. Never recursively delete
  a mount point as cleanup.
- Backup, unmount, and ignition-aware processes interact. Inspect the deployed
  scripts, active jobs, locks, flags, and mounts before forcing an operation.
- A CAN adapter may be connected to a live vehicle network. Do not transmit CAN
  frames unless explicitly requested and the target channel is verified.

## Pi Python package deployment

Dashboard, video-library, audiobooks and BME280 use allowlisted immutable
package releases owned by `pi/deploy_python.py`. First cutover of each service
requires `--activate --service <unit>`; routine `--update` refuses unactivated or
flat-restored services. Backups under `pre-package-units/<unit>.backup/` are
write-once; preserve the earlier dashboard backup too. Per-service state and
pending restart intent survive partial installs and failed reloads/restarts.
See `pi/docs/deployment.md` for exact supervised cutover and saved-unit rollback.
The entire `/home/pi/scripts/python-automation/` directory is frozen: never write
or delete it. `--legacy-flatten` and the old video deployer (including v1 rollback)
now refuse; do not bypass them with an older deployer.

Routine `pi/sync_scripts.sh` stays pinned to `/Users/jacobr/dev/scripts` because
it publishes ignored private inputs. Its preflight requires host activation and
all four service-state markers. It installs non-Python dependencies first,
package updates next, compute last. Generic unit staging excludes all package
units using the installer's `SERVICES` table via local `--list-units`. Use a
chosen clone's checkout-relative package installer for scoped updates without
private inputs; provenance records that clone. `--service` selects unit actions,
not release contents: the shared current link and utility code still advance.

GC under `.install.lock` protects current, previous, just-installed, newest three,
and all four verified running releases. Each entrypoint records pinned
`pi.__path__`, PID and INVOCATION_ID in `/run/<service-name>/package-release`.
Missing/stale active records or ambiguous service state retain everything,
including during partial flat cutover/rollback. Record-write failure only warns.
Factory smokes/custom long-lived consumers must hold a shared installer lock for
their full lifetime and stop before deployment. Never infer a running release
solely from current, previous or the last installer restart.

Do not bundle the root-level `van_compute/` package: its coupled installer owns
the separately imported metrics module. Only `deploy_python.py` owns dashboard backend code and
its unit. The storage installer owns the independent frontend and storage drop-in,
never the flat dashboard; historical manifests containing dashboard backend targets
fail closed, including rollback. The legacy recorder installer refuses both
storage-managed and package-activated hosts. Ship compatible recorder changes first
with the storage installer's recorder-only workflow, then package backend updates;
see the network installer boundary in the runbook. Blueprints
live under `pi/apps/van_dashboard/routes/`; mutable process controllers live in
`runtime.py`, and the relay-off controller CLI remains independent of Flask.

The video allowlist explicitly includes `players/` as a namespace subpackage;
video imports are package-only. Sysmon's shim/package still deploy coherently
under `/home/pi/scripts` via broad sync; only `system_event_monitor.py` keeps the
script/package import switch, exporting `main` and `redact_log_message`. Its
package-release conversion is out of scope. Pi interactive utility consumers
use package current paths; `sns.sh` also preserves the saved flat video's import
path for rollback. Never acquire the installer lock inside a subprocess needed
by a restarting package service (that would deadlock deployment).

## Compute execution contracts

The root `van_compute/` package owns the protocol, queue, broker, Mac worker,
metrics, typed runtime config and shared child/result engine. Host adapters keep
Pi bwrap and Mac sandbox-exec boundaries distinct; staging, ownership, admission,
telemetry and publication are intentionally host-specific. Preserve all 260
compute golden files (258 protocol fixtures plus two child-environment fixtures)
and the host drift decisions in `pi/docs/compute/VAN_COMPUTE.md`. First cutover
must install compute before updating the dashboard package: its unit/imports
require `/home/pi/van_compute/current/van_compute/metrics.py`. Broad sync's
package-before-compute order is not a first-cutover workflow; its earliest
preflight now checks the installed canonical metrics provider and refuses
missing/unsafe providers or maintenance/upgrade-owner fences before any writes.
Keep the compute installer in the owner's Terminal. Reused Pi releases are
verified before drain, ignoring only runtime bytecode. Mac repair/`--rebuild`
creates a new immutable `<deployment24>-<build_uuid32>` generation, never mutates
retained releases, and skips pruning; legacy unsuffixed releases remain valid.

The Mac `.py`/`.zsh` installer paths stay stable; implementation lives in
`macbook/scripts/van_compute_installer/`. Its allowlisted modules travel with
frozen installers, including recovery/rollback copies. `van_compute.engine`
reexports shared child supervision from `engine_process.py`; host adapters still
own staging and publication. Shared compute test fixtures live beside the suites
in `pi/tests/compute/van_compute_test_fixtures.py` and deployment support modules.
Failed Mac builds are disposable only when this invocation owns the generation
and no installed/prior LaunchAgent reference protects it. Once Pi cutover starts,
only the remote cutover script may remove its stage: local SSH interruption does
not establish that the remote script stopped. Dashboard-selected package updates
run the existing compute-provider preflight before transferring a package.

The worker's host-wide retry gate applies only to transport outages, across all
four control connections and all ten slots. Cooldowns cap at 30 seconds and the
exclusive nonmutating recovery probe at 10 seconds, including connection setup;
bulk transfers run outside that exclusive probe so heartbeats can progress.
It never blindly replays an upload or finish; exact-slot lease recovery remains
authoritative. Drain cancels new
admission/waiting claims, not active jobs. Keep `limited_child.py` standalone and
free of package imports or untrusted startup PYTHONPATH.

## Deployment commits

- After a successful deployment from this repository, commit and push the
  deployed repository changes once validation passes. Keep unrelated worktree
  changes unstaged and out of that commit.

## Hosted project directory

The title-menu defaults live in the React `features/projects/catalog.ts` file.
User-added links are persisted separately under `hosted_projects` in the
dashboard's existing StateStore JSON, shared by LAN and Tailscale clients.
Preserve that runtime state during deployment. Links are HTTP/HTTPS only and
are never fetched by the backend; preference is local, LAN, Tailscale, then web.
The `projects` blueprint owns `/api/hosted-projects`; `runtime.hosted_projects`
shares the existing state store and is initialized immediately after it.

## Dashboard response schemas

The React frontend's USB, network and storage response schemas live in each
feature's `schema.ts`, with shared primitives and the only Valibot parsing
boundary in `pi/apps/van_dashboard/frontend/src/api/schema.ts`. Preserve
fail-fast `TypeError` wording and check order; error messages reach tiles and
toasts. Response types are inferred from schemas, while UI-only computed types
remain presentation types. Other features retain their decoders where staged
schemas add no real savings.

`pi/apps/van_dashboard/frontend/src/test/parity.ts` generates fixture/path
mutations. Its compact oracles retain a corpus-name hash, interned rejection
messages and canonical output fingerprints. Prove old/new strict parity, freeze
OLD results and commit before deleting a decoder. Final parity tests read those
files without a snapshot-update write path; never silently regenerate them from
the replacement.
The production boundary is `Response.json()`; sparse JS arrays, accessors and
Proxies are outside that wire contract. Native arrays do not preserve hole
semantics from the old `.map()` decoders.

## Backup and disk tooling

The active design is represented by:

- `pi/scripts/backup/pi_backup.sh`: backup orchestration.
- `pi/scripts/backup/backup_watchdog.sh`: freshness and health monitoring.
- `pi/scripts/backup/abort_backup.sh`: coordinated termination before unmounting.
- `pi/scripts/backup/clone_to_sd.sh`: bootable spare-card cloning.
- `pi/scripts/mount_disks.sh` and `pi/scripts/umount_disks.sh`: disk lifecycle.

An older mount-directory cleanup implementation once treated a failed filesystem
probe as proof that a mount was absent, then recursively deleted the live mount.
The safeguards added afterward—mount verification, explicit utility paths,
fail-closed behavior, and non-recursive directory removal—are critical. Do not
weaken or bypass them.

Keep bootable spare generations staggered so a bad current configuration does
not immediately propagate to every recovery card. Revalidate the live schedule,
capacity limits, labels, and deployed versions before changing clone behavior.

## System monitor reports

Health reports must preserve full-range event counts without loading every raw
event into Python. Keep the covering report index and targeted power/USB queries;
the dashboard shares bounded cached reads across clients. Schema/index updates
belong to collector initialization, never read-only report requests. See
`pi/docs/monitoring/SYSTEM_MONITOR.md` for details.

## Network flight recorder

For network troubleshooting or disconnect diagnosis, start with
`pi/docs/networking/NETWORK_FLIGHT_RECORDER.md` and a read-only recorder report
covering the incident's time range. Use the dashboard's Network History view or
the CLI `report`/`export` commands. Check source freshness, historical-import
coverage and retention, then inspect supporting observations and provenance
before drawing conclusions or proposing changes. Do not assume that an empty
timeline proves uninterrupted connectivity.

`pi/scripts/network_flight_recorder.py` and `pi/scripts/network_recorder/`
consume existing probe/syslog evidence, the antenna's uptime log, and selected
system-monitor context. Collection runs independently of dashboard requests in
`network-flight-recorder.service`. Its active SQLite database and durable router
logs live under `/mnt/EXFAT512/vanpi-network/`, on the verified flash filesystem.
The CLI/dashboard select storage automatically using
`/etc/vanpi-network-storage.json`; inspect the `storage` coverage entry rather
than assuming the flash drive is present. Missing or failed flash uses a bounded
volatile database and raw-log ring under `/run/vanpi-network/`, never SD fallback.
RAM evidence does not survive reboot. The old `/var/lib/vanpi-network/` and
`/var/log/openwrt/` are frozen migration recovery copies, excluded from Borg.

A RAM warning can mean failed log access/replay even with healthy mounted flash;
inspect `storage-status.json`'s error category and the recorder journal. Preserve
the receiver spool's `02750 root:adm` directory and bounded installer permission
repair: Pi rsyslog size-limit rotation otherwise recreates unreadable files.
Do not add tmpfiles `z` repairs through this Pi-owned/runtime, root-owned/spool
hierarchy; systemd rejects that ownership transition.
Rotation tests must use separate root-writer and unprivileged `adm`-reader
identities. Use `deploy_network_storage.py check/apply --recorder-only` for
recorder code/permission updates that must preserve an independently deployed UI.

The storage installer's read-only sysmon prerequisite pins live in
`pi/network_storage_dependencies.py`; its dependency allowlist reuses the legacy
recorder installer's package list. These files remain broad-sync-owned, not
storage-managed targets. Fresh checks pin them; historical rollback manifests
may omit the pins.

Keep reports read-only and full-range counts independent of displayed row
limits. Schema/index changes and incident materialization belong to the
collector. Preserve physical-record checkpoints, backfill provenance and
boot/clock boundaries when changing ingestion or correlation.
Drain pending RAM observations before resuming backlog import or RAM pruning
after flash returns; otherwise incoming history can evict records awaiting replay.

Legacy OpenWrt log timestamps are Pi receipt times; the additive JSON spool
retains receipt and reported times separately. Classic syslog's reported clock
is unverified, and antenna time is approximately anchored from boot/uptime.
Never substitute import time for historical occurrence. A stale source or
collection gap is not a confirmed internet outage; a down/disabled uplink is
not by itself a measured failure. Preserve existing probe budgets, mwan3 marks
and the shared-DNS limitation.

Use the guarded, targeted `pi/deploy_network_storage.py` installer and
manifest-specific rollback; the older recorder installer refuses storage-managed
or package-activated deployments. Preserve mount identity checks, bounded RAM
buffering and idempotent recovery. Never write through an absent mount or unlink
a database beneath active
readers. Avoid repository-wide sync for recorder updates; `pi/sync_storage_managed.exclude`
keeps the broad sync from staging any file this installer owns, so add new storage-managed
targets there too.
See `pi/docs/networking/NETWORK_FLIGHT_RECORDER.md` for commands, limits and
remaining evidence gaps.

## macOS video thumbnails

On affected macOS versions, Finder may select frame zero for videos that fade in
from black, producing black thumbnails even though the media and SMB share are
healthy. `macbook/scripts/stamp-video-thumbs.zsh` uses a `qlmanage` thumbnail as
a Finder custom icon. It requires a GUI session and may write AppleDouble
resource-fork files on SMB storage.

After macOS updates, test whether native frame selection is fixed before stamping
more icons. Remove custom icons and clear the thumbnail cache deliberately if the
workaround is no longer needed.
