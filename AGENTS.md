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
- Visual Guides is maintained separately in `~/dev/visual-guides`, public repo
  `https://github.com/JakeIwen/visual-guides`. It serves Mac loopback port 8792
  and Pi port 8791. Its own `deploy.py` owns `/home/pi/visual-guides/` releases
  and `visual-guides.service`; never restore a copy under `pi/projects/`.
  The dashboard's project catalog links to both hosts; preserve runtime-added
  links. A/C commands and GPIO18 setup belong to that repo; GPIO17 remains the
  alert-light owner here.
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
  Performance Audio, Convert Video, Custom Conversion, BPM Over Time, Rhythm
  Practice and Interval Analysis are maintained separately in `~/dev/mac-media-tools`
  (public repo `https://github.com/JakeIwen/mac-media-tools`).
  That repository owns their scripts, shared Finder app, BPM runtime, jobs, custom
  presets and generation-keyed reports; do not restore them here. BTT menu
  installers remain under `macbook/bettertouchtool/` and use
  `btt_common.MEDIA_TOOLS` (`MAC_MEDIA_TOOLS` override); its read-only
  `--media-tools` CLI also supplies the thumbnail tool's FFmpeg fallback.
  `macbook/tests/btt_test_support.cjs` supplies temporary suite fixtures for
  installer tests, independent of either live checkout.
  `tools_menu_item.py` and `install_tools_menu_item.js` own the shared definition
  and guarded API worker for standard Media > Tools items; the feature-named
  `install_*_menu.py` entry points remain compatibility wrappers.
  `macbook/bettertouchtool/btt.py` is the maintenance CLI; `btt_common.py/.js`
  share read-only inspection and backup helpers. `repair_media_actions.py`
  defines the action-recovery wire contract consumed by its JXA worker.
  `macbook/bettertouchtool/btt_guard/` owns explicit known-good checkpoints,
  read-only integrity audits, approved restoration, ntfy warnings and periodic
  launchd control. Its `model.py` owns shared contracts/statuses; `storage.py`
  owns private atomic state. Never promote audit output into the known-good
  pointer automatically. Checkpoint integration fixtures live in
  `macbook/tests/btt_guard_fixtures.py`.
- `shared/`: code imported or deployed on more than one host.
- `pi/scripts/build_claude_code_proxy.sh` builds `claude-code-proxy` from source for
  Claude Code's Model Gateway (the release binary needs glibc 2.39). Never run the
  gateway's `setup` on the Pi; `pi/scripts/model_gateway_setup_guard.cjs`, preloaded
  via `NODE_OPTIONS` in `pi/.bashrc`, blocks it. See
  `pi/docs/compute/CLAUDE_MODEL_GATEWAY.md`.
- `pi/apps/van_dashboard/van_dashboard_ubnt_recovery.py` reconciles uncertain
  Starlink antenna operations against fresh radio state without replaying them.
  Frontend `features/ubnt/UbntOperationNotice.tsx` distinguishes pending
  confirmation from an actual failure; the CLI emits `confirmation_pending`.
- `pi/scripts/system_monitor/coalesce.py` owns schema-v5 event columns, the
  60-second USB/storage journal windows and replay watermarks. Other categories
  remain append-only for the network recorder; preserve its `events_report_idx`.
  `locking.py` coordinates
  collector writers with exclusive offline maintenance; `maintenance.py` owns
  the dry-run/apply compaction CLI, batched folding and space-checked VACUUM.
  Retain only current-boot legacy fingerprint tombstones; ordinary pruning
  expires 30-day snapshots through the collector-owned partial snapshot index
  without deleting noteworthy events. Reports sum
  represented counts through the covering index; aggregate ranges use their
  first timestamp (minute granularity). The dashboard storage reader resolves
  snapshot references; read-only event readers detect v4/v5 columns explicitly
  and never turn lock or I/O errors into unweighted counts.
- Network storage deployment test filesystem fixtures live in
  `pi/tests/network/_network_storage_support.py`.
- `pi/tests/dashboard/_frontend_test_support.py` provides a temporary React
  index response for route tests, independent of deployed frontend builds.
- Deferred video checkpoints are indexed once per identity-sync transaction by
  `pi/apps/video_library/deferred_positions.py`; preserve chronological replay,
  exact path matching and removal after successful resolution. Scan/query and
  playback-lock regression tests live in `pi/tests/media/test_video_scan_*.py`.
- Physical-file identity matching and observation persistence live in
  `pi/apps/video_library/file_observations.py`; the catalog owns their transaction
  and performs location versioning only after all observations agree.
  `filesystem_identity.py` resolves Linux mount devices through `/dev/disk/by-uuid`
  and stores `fsuuid:<UUID>` in the existing TEXT `device_id` column (schema v3).
  Numeric history is retained; only live exact active paths with matching inode,
  size and known nanosecond mtime can promote it. UUID resolution fails closed on
  Linux; non-Linux numeric observations are not advertised as reboot-stable.
  Identity matching requires equal size and compatible mtime; growing files need
  an exact torrent locator for continuity, not an inode-only exception.
  Older packages can open this schema but their matcher remains unsafe: rollback
  requires quiescing the service and restoring the matching pre-change backup.
- Completed October 2026 video-catalog repairs are documented in
  `pi/docs/media/VIDEO_LIBRARY.md` under "Catalog repair history". Their one-time
  executables and exclusive tests are retained in Git history; runtime identity
  matching and its regression tests remain in the active package.
- Cloud holds: `pi/scripts/backup/cloud_backup_control.py` shares admission and
  durable `pause.json` controls. `icloud_backup_control.py` and
  `time_machine_icloud_control.py` are fixed Pi/Mac entry points; their separate minute resume timers,
  worker and Mac heartbeat all honor it. Dashboard mutations use
  `van_dashboard_cloud_controls.py`. Samba mount reconciliation clears only
  empty shutdown gates; nonempty capture gates are released by their owner.
  Indefinite holds use `paused_until: "indefinite"`. Switching clouds locks both
  control records, pauses the peer indefinitely, then queues the selected job;
  never delete or force-release the common backup lock or stop local backups.
  Frontend cloud labels/estimates live in `features/backups/cloudPresentation.ts`;
  `ICloudPauseSelector.tsx` owns the shared pause split button: the main action
  pauses indefinitely, while choosing a duration applies a timed pause immediately.
- Cloud failure popups: `pi/apps/van_dashboard/cloud_backup_details.py` reads
  bounded journal evidence for a retained Pi/Mac attempt selected by server-owned
  history. Only recognized worker diagnoses are exposed; raw journal/provider
  text stays private. `features/backups/ICloudAttemptMessage.tsx` owns the popup,
  with its response contract in `attemptDetails.ts`. Neither endpoint nor popup
  changes worker state, and nested dialogs close independently.
- Backup priority: `pi/scripts/backup/backup_priority.py` owns the cloud job
  registry and the protected until-verified override; `backup_priority_control.py`
  is the fixed CLI. Priority only decides which cloud job may take the single
  iCloud upload slot; local Borg/EXFAT and disk jobs are always admitted. Mount,
  ignition, uplink and manual-pause checks remain. Verification completion
  is latched so a later generation cannot reactivate an old override.
  `mac_capture_control.py` issues expiring, one-use stop permission bound to a live
  capture nonce. The root Mac coordinator verifies the active destination before
  `tmutil stopbackup`, then a later poll must prove idle and non-force detach.
  Dashboard `van_dashboard_backup_priority.py` and `routes/backup_priority.py`
  serve the gear-menu UI in `features/backups/BackupPriorityMenu.tsx`; `priority.ts`
  owns its response contract. Both targeted cloud installers ship these modules
  and verify the shared backup configuration before updating it.
- `pi/scripts/backup/protected_json.py` owns private control-record metadata
  checks and bounded, no-follow descriptor reads for priority state; each caller
  keeps its own schema validation and reuses the existing atomic writer.
  Both targeted cloud installers ship it alongside `backup_priority.py`.
- `pi/tests/backup/priority_fixtures.py` owns isolated priority/cloud-directory
  fixtures without importing cloud workers; priority regressions reuse it.
- Backup concurrency: `pi/scripts/backup/backup_locks.sh` (sourced by
  `backup_conf.sh`) and `backup_locks.py` own the resource lock registry in
  `/run/lock/vanpi-backup/`, pre-created by `pi/tmpfiles.d/vanpi-backup.conf`;
  `pi/docs/backup/CONCURRENCY.md` is the conflict matrix. Each job holds an
  exclusive `job-<kind>` lock (fd 10, PID record), shared `disk-<label>` locks
  for the disks it uses, `data-borg-repo` while mutating or copying the Borg
  repository, and the cloud workers hold the exclusive `cloud-upload` slot for
  their whole run (taken by the shell wrapper before `exec`, so a busy slot exits
  before any attempt is recorded). Migrated jobs hold the legacy
  `/run/lock/vanpi_backup.lock` shared on fd 9; `restore_from_borg.sh`,
  `new_hotspare.sh` and the deployment fences keep `acquire_job_lock` exclusive,
  so legacy consumers exclude and are excluded by every job. Never create lock
  files at runtime, never probe with path-form `flock`, and never drain a disk
  that the caller is not about to unmount or spin down: `umount_disks.sh` passes
  the labels it unmounts (with `--spindown`, every attached HDD, since a job
  past its ignition check may hold only a disk lock while it is still mounting)
  to `abort_backup.sh`, which TERMs only job parents holding those disks
  (and any legacy exclusive holder), waits for each disk lock, and in emergency
  mode kills remaining holders of those lock files only. Local jobs re-check
  the ignition flag after taking their locks and before mounting, since the
  drain reaches only lock holders. `--user borg|exfat`
  targets that kind's job lock. A job that mounted a disk unmounts it only when
  `release_backup_disk_if_unused` proves no other holder. `backup_window.sh`
  starts Borg and EXFAT concurrently. The former local-fairness lease, supervisor
  and rollback helper are retired; a stale `local-fairness.json` is ignored.
  `pi/backup_local_deploy.sh` shares the local payload and reviewed-source hash
  checks for both targeted cloud installers; `VANPI_BACKUP_PREVIOUS_REVISION` names
  the previously deployed commit when installing a newly committed change.
  `VANPI_BACKUP_PREVIOUS_CONF_SHA256` accepts an explicitly reviewed config hash
  when unrelated deployed settings differ from that commit. Previous-tree absence
  permits only a missing or exact staged live file.
  `pi/tests/backup/test_local_backup_deploy.py` exercises those shell contracts
  against temporary source/live directories and a fixture Git repository.
  Real-process lock regressions live in `pi/tests/storage/test_backup_locks.sh`
  and `pi/tests/backup/test_backup_locks.py`.
- `pi/scripts/backup/icloud_inventory.py` owns cloud object fingerprint parsing
  and size-based upload estimates, re-exported by the shared cloud worker.
- `pi/scripts/backup/time_machine_pipeline.py` supervises a single verifier
  alongside the Mac uploader, preserving the inherited backup lock. Completed
  upload events admit new chunks; the initial remote inventory seeds catch-up.
  `time_machine_verification.py` owns the existing SHA-256 checkpoint format for
  both background and final checks. Only the parent writes dashboard state and
  publishes completion, after an exhaustive final inventory/fingerprint check.
  `time_machine_downloads.py` batches streamed rclone SHA-256 downloads, validates
  each completed hash before checkpointing, and measures a rolling rate including
  waits. `icloud_progress.TransferActivity` defines safe verification activities.
  `icloud_uplink.py` collects only fresh route/selected-association evidence;
  never substitute cached dashboard health or skip selected antenna checks.
- `pi/scripts/backup/time_machine_staging.py` owns local orphan-chunk cleanup.
  Plan/apply require the common backup lock, verified mount and owned store,
  preserving capture-index, pending/published manifests and upload/verification
  references. Cleanup runs before new captures; never delete the live image or
  archived backups. First-copy transient deferrals retry after five minutes,
  while explicit cloud pause settings retain priority.
- Bootable clone evidence is built in `van_dashboard_hotspares.py`, using the
  existing normalized device-tree helpers in `van_dashboard_block_devices.py`.
  Root usage comes from mounted `lsblk` counters or a read-only, label-verified
  ext4 superblock probe. Never mount a spare merely to render its usage.
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

Dashboard Starlink power-off uses a recent completed full survey, or scans before
disconnecting when the live radio already covers all standard channels, to switch
directly to another saved network. If scan preparation is needed or direct
association fails, it releases denlink and performs a full survey. Keep the denlink
profile and leave unrelated Wi-Fi connections alone. Serialize power intents behind active antenna mutations;
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
solely from current, previous or the last installer restart. Ad-hoc tools that
import a release (PYTHONPATH into `releases/` or `current`) must set
`PYTHONDONTWRITEBYTECODE=1`: GC skips everything when any release tree differs
from its manifest, so one stray `__pycache__` stops all pruning.

Never bundle `van_compute` (now a separate repository): its coupled installer
owns the separately imported metrics module. Only `deploy_python.py` owns dashboard backend code and
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

`van_compute` lives in its own repository, `/Users/jacobr/dev/van-compute`
(public repo `https://github.com/JakeIwen/van-compute`); its `AGENTS.md` holds the compute contracts. This repository
keeps only the consumer side: the dashboard imports `van_compute.metrics` from
the separately installed `/home/pi/van_compute/current`, `pi/check_compute_provider.py`
is the sync/package preflight against that installed provider, and
`pi/sync_scripts.sh` runs the compute installer from `VAN_COMPUTE_CHECKOUT`
(default `/Users/jacobr/dev/van-compute`). First cutover must still install
compute before the dashboard package. Dashboard tests require the sibling
checkout, verified by `pi/tests/dashboard/_van_compute_checkout.py`; they fail
rather than skip without it.

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
- `pi/scripts/backup/openwrt_backup.sh`: verified router exports; independent
  daily cron retries use `--daily`, and Borg can also refresh before archiving.
  A directory flock serializes exports without taking the cloud/disk backup lock.
- `pi/scripts/backup/backup_watchdog.sh`: freshness and health monitoring.
- `pi/scripts/backup/rootfs_budget.sh`: bounded rootfs path budgets and daily growth history for the watchdog.
- `pi/scripts/rootfs_tidy.sh`: weekly apt-cache cleanup that runs the shared Codex release retention as root with `--owner pi`.
- `shared/sh/cleanup_codex_releases.py`: Codex standalone release retention for both the MacBook (weekly user cron via the `macbook/scripts/` symlink) and vanpi; keeps three versions plus current/running releases.
- `pi/scripts/backup/abort_backup.sh`: coordinated termination before unmounting.
- `pi/scripts/backup/clone_to_sd.sh`: bootable spare-card cloning.
- `pi/scripts/mount_disks.sh` and `pi/scripts/umount_disks.sh`: disk lifecycle.
  `pi/scripts/backup/umount_disks_fence.sh` (sourced by `umount_disks.sh`, shipped
  with the backup payload) stops and claims backup jobs' disks before unmount or spindown.

An older mount-directory cleanup implementation once treated a failed filesystem
probe as proof that a mount was absent, then recursively deleted the live mount.
The safeguards added afterward—mount verification, explicit utility paths,
fail-closed behavior, and non-recursive directory removal—are critical. Do not
weaken or bypass them.

Keep bootable spare generations staggered so a bad current configuration does
not immediately propagate to every recovery card. Revalidate the live schedule,
capacity limits, labels, and deployed versions before changing clone behavior.

## Backup concurrency rollback

Both targeted cloud installers keep and print `previous/` copies; there is no
separate code-rollback entry point. First arrange quiescence with the existing
controls (pause both cloud jobs, let local jobs finish), preserving independent
holds. Then, with `L=/home/pi/scripts/backup` and `P` set to the saved
directory, validate `/run/lock/vanpi_backup.lock` (`root pi 660`, regular,
not a symlink), open it with `exec 9<"$B"` and acquire it with
`/usr/bin/flock -n 9`; an exclusive acquisition proves no migrated job is
running. For each saved file use the installer's primitives,
`sudo /usr/bin/install -o pi -g pi -m 0750 "$P/$N" "$L/$N.new"` then
`sudo /usr/bin/mv -Tf "$L/$N.new" "$L/$N"`, comparing `sha256sum` before moving
on. Restore the complete cohort (`backup_conf.sh`, `backup_locks.sh`,
`umount_disks_fence.sh` (an older `umount_disks.sh` does not source it),
`backup_window.sh`, `pi_backup.sh`, `exfat_snapshot.sh`, `abort_backup.sh`,
`clone_now.sh`, `backup_priority.py`, `cloud_backup_control.py`, both cloud
wrappers and workers, `icloud_status.py`, `time_machine_staging.py`,
`time_machine_pipeline.py`), plus `/home/pi/scripts/umount_disks.sh` and
`internet_switches.sh` when their saved copies exist, and the previous
`/etc/tmpfiles.d/vanpi-backup.conf`. Release fd 9 only after the complete
cohort. The lock registry directory can stay; old code ignores it. A retired
`local-fairness.json` remains readable by the old fairness code, which ends an
abandoned turn by itself. Verify with
`sudo /usr/bin/python3 -B "$L/backup_priority_control.py" --status`.

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
