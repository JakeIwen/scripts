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

## Contracts

| Path | Caller | Owner |
|---|---|---|
| `macbook/scripts/display_cab_dimmer.py` | `com.jacobr.display-cab-dimmer` LaunchAgent and Mac Panel automation | `macbook/scripts/cab_dimmer/` plus the stable entry script |
| `macbook/scripts/deferred.zsh` | `com.jacobr.deferred-jobs` LaunchAgent and Mac Panel | the script and `shared/automations/` declaration |
| `pi.apps.van_dashboard` | `van-dashboard.service` and `pi/deploy_python.py` | `pi/apps/van_dashboard/` |
| `pi.apps.video_library` | `video-library.service` and `pi/deploy_python.py` | `pi/apps/video_library/` |
| `pi.apps.audiobooks` | `audiobooks.service` and `pi/deploy_python.py` | `pi/apps/audiobooks/` |
| `pi.apps.bme280` | `bme280-mqtt.service` and `pi/deploy_python.py` | `pi/apps/bme280/` |
| `pi/scripts/system_event_monitor.py` | system-event-monitor, crash-capture and network-recorder consumers | `pi/scripts/system_monitor/` plus the stable shim |
| `pi/scripts/backup/` | backup units, timers, cron and scoped deployers | the feature folder and `pi/backup_local_deploy.sh` |
| `shared/sh/` | Mac/Pi aliases, cron and targeted flat Pi deployment | feature-named scripts in the documented compatibility folder |
| `pi/projects/phone-drop/` | `phone-drop.service` and its scoped deployer | the project folder |
| `pi/projects/youtube-cast/` | `youtube-cast.service` and its scoped deployer | the project folder |
| `ubnt/persistent/scripts/` | airOS boot hooks and `ubnt/scp_to_device.sh` | the complete Wi-Fi-manager cohort |

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
- `pi/projects/phone-drop/`: Phone Drop, the token-protected upload inbox for
  the iPhone share sheet (`phone-drop.service`, port 8793). Its own `deploy.sh`
  owns `/home/pi/phone-drop/`, the unit and the token; broad sync never ships it.
  Uploads land in `~/Documents/agent-shared/from-phone/` under an 8 GiB quota.
- `pi/projects/youtube-cast/`: Van TV, the YouTube DIAL/Lounge receiver
  (`youtube-cast.service`, DIAL port 8794, LAN/loopback status port 8795).
  Its own `deploy.sh`/`controller.sh` own `/home/pi/youtube-cast/` and the unit;
  broad sync never ships it. `src/config.mjs` owns player tuning and ports,
  `mpv-ipc`/`mpv-process`/`mpv-player` own playback, `process-command` owns
  bounded subprocess groups, `vlc-handoff` preserves library bookmarks, and
  `private-data-store` preserves screen identity/settings in private state.
  `stable-dial-uuid` pins the SSDP device UUID across restarts; `video-id`
  is the one video-id check; `player-controls` owns the `/api/control`
  actions. `pi/.bashrc`'s `vantv_*` functions route the BTT media buttons'
  ssh commands (`vlcmd`, `pp`, `play_status`, `kill_media`) to Van TV while a
  cast is loaded. Mac senders use the status server directly:
  `macbook/scripts/vantv.zsh` (`vantv` alias) casts a link or the front
  Chrome/Safari tab over `/api/cast` and controls playback over `/api/control`.
- `macbook/firefox/van-tv/`: private unsigned Firefox Developer Edition
  extension for Van TV. `config.js` owns receiver addresses and shared vocabulary;
  `youtube-video.mjs` parses video URLs; `receiver-client.mjs` owns background-only
  HTTP; `tab-casting.mjs` owns playhead handoff; `background-actions.mjs` wires
  messages, menus and shortcuts. `content.js` owns the YouTube player button;
  `popup.mjs`/`popup-state.mjs` own receiver controls. `build.zsh` packages the
  dependency-free XPI into ignored `dist/`; `tests/*.test.mjs` run through the
  repository suite.
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
  `macbook/scripts/shimg.zsh` is called by `~/.zshrc`'s `shimg` alias;
  it saves clipboard PNGs into the mounted Pi `agent-shared` folder, with
  an optional filename (default `screenshot.png`).
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
  owns private atomic state. Never promote audit output during ordinary audits
  or Guard repairs. There are two narrow exceptions. `btt_guard/installer_checkpoint.py`:
  a wired installer's `--apply` may advance an existing checkpoint only after a
  healthy pre-write baseline, saved-state verification, an unchanged checkpoint
  id and a final audit whose findings are confined to the UUIDs it wrote; zero
  writes, unavailable state, foreign findings or a failed capture never promote.
  Wired installers expose `--no-checkpoint`. `btt_guard/edit_acceptance.py` (Jacob's
  choice): the scheduled monitor (`monitor --managed`, a gui-domain LaunchAgent
  because capture needs Apple Events) accepts drift that appeared while the same
  BTT process (pid + start time), version and checkpoint stayed in place since the
  last healthy scheduled audit, once it is unchanged for 10 minutes; drift after a
  BTT relaunch or update, unavailable audits and captures that refuse or fail are
  alerted, never accepted or retried. Manual `monitor` passes never accept.
  `guard acceptance` prints its state read-only for the Mac Panel.
  `btt_guard/retention.py` keeps history 90 days: the scheduled monitor prunes
  inactive checkpoints and rejected captures older than that once a week, never the
  active checkpoint (`guard prune` previews, `--apply` deletes). Checkpoint integration fixtures live in
  `macbook/tests/btt_guard_fixtures.py`. `btt.py guard last-audit` prints the
  saved audit report read-only (`{"status": "none"}` when there is none).
  `repair_text_transforms.py/.js` owns the guarded text-transform installer;
  `text_selection.js` owns AX selection handling and `text_menu_paste.js` owns
  clipboard-preserving native Paste for editors with broken AX replacement.
  `text_markers.js` preserves Firefox text-node positions during reselection;
  never round-trip rich-editor selection endpoints through flat offsets.
  `signal_text_diagnostic.js` owns the temporary Signal diagnostic button's runner.
  `macbook/crontab` is the tracked user crontab; `macbook/tests/test_crontab.py`
  checks that every repo script it runs (paused `#off ` jobs included) exists and
  is executable. `macbook/scripts/crontab_sync.py` owns its parsing, stable job
  ids, `#off ` pauses, revision-checked writes, drift-safe install (refused while
  the live crontab has lines the file lacks) and private live backups in
  `~/.local/state/crontab-sync/`; llm-restore keeps its own switch.
  `macbook/scripts/deferred.zsh` owns one-off future jobs on the Mac (queue,
  hourly/login checker LaunchAgent `com.jacobr.deferred-jobs`, purge on success,
  `NTFY_WARNING_URL` on failure, daily cron watchdog); queue jobs with its `add`
  instead of writing a LaunchAgent per job. Usage is in `macbook/README.md`.
  Its `list --json`, `retry`, `run-now` and `reschedule` serve the Mac Panel;
  every mutation (and `cancel`) takes the checker's own flock and exits 75 when
  it is busy, and `run-now` never sends a failure alert.
  `macbook/mac_panel/` is the Mac Panel, `http://127.0.0.1:8769/` (port 8769;
  `macpanel` alias in `~/.zshrc`; BTT menu-bar icon and ctrl+opt+cmd+P from
  `bettertouchtool/install_mac_panel_menu.py`): a stdlib server run by the login
  LaunchAgent `com.jacobr.mac-panel` (gui domain, venv
  `~/.local/share/mac-panel/venv`; `install.zsh`, controller `panel_service.py`
  with prepare/install/status/start/stop/restart/verify/uninstall; runtime files
  in ignored `tmp/mac-panel/`). One adapter module per section, wire records in
  `panel_contracts.py`, request guards (Host allowlist, per-instance token,
  Origin + JSON on POST) in `request_guard.py`, the only subprocess boundary in
  `cli_runner.py`, display redaction in `log_redaction.py`. It changes tools only
  through their own CLIs or `tool_settings` (crontab edits go through
  `crontab_sync.py`); ntfy config is read-only there, BTT repair is never
  exposed, and it never reads pi/secrets. Its Automations section
  (`automations_adapter.py`) needs no per-tool panel code: it lists
  `shared/automations/<tool>.json` declarations, runs each one's status command
  and the declared actions (synchronous, confirmed when declared) by fixed argv.
  Its page is ES modules in `static/` with no build step: `app.mjs` (section
  registry, tabs, polling, open-tab/theme state, panel health), `panel_sections.mjs`
  (`refresh`/`action` and the dirty check polls obey), `panel_api.mjs` (the only
  `fetch`), `panel_dom.mjs` (DOM helpers, armed buttons, notices, times, focus
  keys), `relative_time.mjs`, `status_cell.mjs` (`Level` vocabulary) and one
  `section_<name>.mjs` per section. `server.py` serves any lower-case `.mjs`/`.css`
  in `static/`; `tests/test_static_page.py` checks each module parses and its
  imports resolve. Service log tails load on demand from
  `GET /api/services/logs/<label>`, and `/healthz` reports `source_changed` so the
  page offers a restart after Python edits.
  Usage: `macbook/mac_panel/README.md`.
  `macbook/scripts/ntfy_warning.py` is the one sender for `NTFY_WARNING_URL`
  warnings (sourced privately from `pi/secrets/.bash_variables`, never in argv
  or logs): btt_guard imports `send_warning`, deferred.zsh runs it with the
  message on stdin. Reuse it rather than reading the URL again.
- `shared/`: code imported or deployed on more than one host.
- Tool settings: each configurable tool declares its settings once in
  `shared/settings/<tool>.json` (name, type, range, default, description);
  `shared/python/tool_settings.py` (stdlib, runs on `/usr/bin/python3` 3.9) is
  the only reader/validator/writer, used by both the tool and the Mac Panel.
  Values live in `~/.config/jacobr/<tool>.json` (mode 600, atomic, only
  non-default values, revision-checked); long-running tools reload them with
  `tool_settings.Reloader`, which keeps the last good values when the values or
  declaration file can't be read. A setting may carry an optional `label` (form
  wording; `name` stays the key). LaunchAgents need `PYTHONPATH=.../shared/python` in
  their plist. First user: the iTerm font follower (`iterm-screen-fonts`). The
  display cab dimmer (`macbook/scripts/display_cab_dimmer.py`, LaunchAgent
  `com.jacobr.display-cab-dimmer`) dims only cab Wiz lights that are on through
  the dashboard API and never alerts; its settings (including `enabled`) are
  declaration-driven like `iterm-screen-fonts`, and it is the first automation.
  The stable script owns CLI/watch/dashboard orchestration;
  `macbook/scripts/cab_dimmer/model.py` owns display records and plug-in rules,
  `state_store.py` owns locked private state, and `displays.py` owns lazy
  CoreGraphics enumeration so status-only commands do not import macOS bindings.
- Automations: `shared/python/tool_automation.py` (stdlib, 3.9) is the one
  definition of a panel automation: the `shared/automations/<tool>.json`
  declaration (title, optional service label and settings tool, a status
  command and actions as argv with `{repo}`/`{home}` placeholders and
  timeouts of at most 120 s) and the status record a tool's status command
  prints (`status_record`; levels mirror `status_cell.mjs`'s `Level`). The
  panel only reads declarations and relays actions; each tool keeps its own
  durable state (the dimmer's is `~/.local/state/display-cab-dimmer/`).
- `shared/sh/session_search_header.sh` formats the shared activity-age/name header
  and quoted directory/resume command for `claudesearch.sh` and `codexsearch.sh`.
  It also provides `-h`/`--help`, parses `-g`/`--group`, and groups matches per transcript, retaining
  the last matching excerpt in append order. `--` ends option parsing.
  Age uses transcript modification time; ship the helper and
  `shared/sh/session_search_records.jq` with the search scripts. The jq module owns
  their internal match/summary records and directory/tool aggregation for
  `shared/sh/llmsearch.sh`. Its `-g` totals matches across sessions and resumes
  the session with the latest matching event (mtime fallback without timestamps).
  `shared/sh/session_search_paths.py` lists transcript paths by newest activity
  before contents are searched, interleaving providers for `llmsearch`.
  Ungrouped excerpts run newest first within each session. Both Python helpers
  require Python 3.9+: `shared/sh/session_search_display.py` incrementally groups
  grep records and redraws `llmsearch -g` on a terminal; pipes get one final plain
  listing. Directory groups and their provider entries are newest first.
  `llmsearch` asks LLM Switchboard's transcript index first (`llm-switchboard
  search --json`, Pi only), labels its results with the same helpers and groups
  them with the same jq function. `--grep`, a non-default session root, records
  mode, a missing command or exit 3 (no finished index build, asked twice) grep
  instead. The llm-switchboard repo's `tests/test_search_oracle.py` runs these
  scripts in records mode, so keep that format and those grep triggers.
  The Pi runs these seven search files from `/home/pi/scripts/`; `pi/.bashrc`
  defines all three commands. For targeted updates, ship the complete set and
  patch only the command definitions in the live `.bashrc`, preserving local changes.
- `shared/sh/llm_restore.py` (alias `llmrestore` on both hosts; Pi copy at
  `/home/pi/scripts/`) reopens the Claude Code and Codex sessions open before a
  reboot, skipping any already open or kept by llm-switchboard: iTerm tabs on
  the Mac, tmux session `llmrestore` on the Pi; `--pi` on the Mac reopens the
  Pi's as iTerm tabs over SSH (the Pi chooses them). Its every-minute snapshot
  is a `managed llm-restore` block tracked in `macbook/crontab` and
  `pi/crontab_pi` (its test keeps both equal to `cron_block()`); `--cron off|on`
  pauses and resumes it with a flag file instead of editing the crontab;
  `--cron [on|off] --json` prints one status object for the Mac Panel, while
  read-only `--list --json` exposes the currently detected sessions and TUI pids.
  State and the generated restore script live in `~/.local/state/llm-restore/`.
- `shared/sh/uncommitted_owners.py` implements `+id`: it attributes dirty Git
  paths to recent Claude Code and Codex tool calls, rolls subagents up to their
  resumable parent, names open sessions' terminal location and prints resume
  commands for closed sessions. It uses `llm_restore.py --list --json` for Codex
  open-session detection and degrades to unknown when that listing fails.
- `shared/sh/plus_commands.py` prints Jacob's + commands: the first sentence of
  each bullet in the global instructions' "+ commands" section. With `--hook`
  it is the UserPromptSubmit hook (Claude and Codex, Mac and Pi) that answers a
  bare `+cmd` without a model turn and passes every other prompt through.
  `uncommitted_owners.py` and `plus_commands.py` sit flat in `/home/pi/scripts/`
  on the Pi, next to `llm_restore.py`.
- `shared/claude-mods/clean-output/` is the Claude Code mod behind `/clean`
  (usage in its README). Its engine-free modules (`hooks/*.ts` except
  `register.tsx`) also make `codexclean`, the same view of a Codex session:
  `codex/build.mjs` strips their types (Node >= 22.13) into plain modules that
  run on the Pi's Node 20, and `zsh codex/deploy.zsh` installs them on the Pi,
  then the Mac, in `~/.local/share/codexclean/` with a launcher at
  `~/.local/bin/codexclean`. The installed copies don't follow the checkout:
  re-run the deploy after editing any of those modules.
- Pi video audio is hardwired: HDMI-A-1 → splitter box (HDMI to the monitor,
  audio to optical) → Sonos soundbar's optical input. `sns.sh rear_movie`
  switches the soundbar to optical and groups vonRear with it; VLC and Van TV's
  mpv play to ALSA HDMI 0, and PipeWire's null sink is expected, not a fault.
- Dashboard Sonos alarms live in `pi/apps/van_dashboard/sonos_alarms.py`, with
  HTTP routes in `routes/sonos_alarms.py` and React controls in `features/sonos/`.
  `SonosAlarm` owns the device record; `alarms.ts` owns the validated UI contract.
  `sonos_clock.py` labels the speaker's reported clock zone, with UTC-offset fallback
  for unrecognized rules; never substitute the browser or Pi timezone.
  `alarmFilters.ts` owns weekday and enabled-state filtering, using the speaker clock for one-time alarms.
  `alarmControls.ts` is owned by `SonosFeature`, so closing the sheet or collapsing
  alarms cannot release an in-flight write. Delete and enable are explicit commands.
  Read fresh device state before mutations, preserve existing music metadata,
  reject stale revisions, and never automatically retry an uncertain alarm write.
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
- `pi/tests/dashboard/_disk_manager_support.py` holds the DiskManager fixture (label policy, lsblk
  tree, boot ids) shared by the disk tests and `test_disk_storage_events.py`.
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
  `pi/tmpfiles.d/vanpi-samba-drain.conf` recreates the root-owned capture gate
  directory at boot; the Mac worker also applies it in ExecStartPre. Setup must
  preserve existing gate files. Both scoped installers ship and apply it.
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
  serve controls in `features/backups/BackupPriorityControls.tsx`; `priority.ts`
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
  `backup_locks.py` defines the canonical names. The build-time
  `pi/generate_backup_lock_registry.py` emits `backup_lock_registry.sh` and the
  tmpfiles file; run `python3 -m pi.generate_backup_lock_registry` after changing
  names. Both targeted installers and broad sync check for drift before staging.
  `backup_lock_io.sh` opens existing shell lock inodes, verifies their validated
  identities and writes PID records through the locked descriptor.
  `backup_admission.py` owns the validated cloud-run descriptor bundle and its
  scoped client context. Both `--run` entries validate before persistent writes;
  clients and the verifier carry that bundle, plus an explicitly checked Borg
  data descriptor during the repository copy. `require_held` only asserts Linux
  fdinfo evidence; it never acquires a missing hold.
  `backup_lifecycle.sh` validates the full target set, drains it and retains
  claims in the caller through mutation. `stale_mount_recovery.sh` owns the
  common manual/automatic recovery operation and original label/target/source
  records. Policy sources the mount library in its bounded subshell and supplies
  its consumer callback; manual `--recover-stale` uses the same claim operation.
  Refreshed discovery must not widen that set. Manual recovery still uses an
  ordinary unmount and refuses busy consumers rather than force-closing them.
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
  `pi/backup_local_deploy.sh` shares the remote contract for both targeted cloud
  installers. `backup_deploy_plan.sh` is their fixed source/destination, review,
  ownership, mode and first-install plan; `backup_deploy_transaction.sh` stages
  the full cohort and private previous tree before publishing, then restores and
  verifies attempted replacements under the fence on failure. Incomplete
  restoration or unit reload leaves timers stopped. `VANPI_BACKUP_PREVIOUS_REVISION` names
  the previously deployed commit when installing a newly committed change.
  `VANPI_BACKUP_PREVIOUS_CONF_SHA256` accepts an explicitly reviewed config hash
  when unrelated deployed settings differ from that commit. Previous-tree absence
  permits only a missing or exact staged live file.
  `pi/tests/backup/test_local_backup_deploy.py` exercises those shell contracts
  against temporary source/live directories and a fixture Git repository.
  Real-process lock regressions live in `pi/tests/storage/test_backup_locks.sh`
  and `pi/tests/backup/test_backup_locks.py`. `backup_lock_fixtures.py` owns their
  private registry/admission fixtures; `test_backup_admission.py` tests the
  actual worker entry boundary and inherited client descriptors.
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
- Hotspare rotation: `pi/scripts/backup/hotspare_rotation.py` owns the two-card
  planner and durable incomplete markers; `hotspare_rotation.sh` verifies both
  labels before dispatch under the existing clone job lock. One older card is
  refreshed per `CLONE_ROTATION_DAYS` (default 7), measured from the newest
  successful clone; missed runs never produce a catch-up batch. Missing, ambiguous
  or mounted peers defer automatic rotation. An interrupted clone retries that
  same target, and no automatic write proceeds without a completed peer. Manual
  Clone now remains an explicit override. The settings CLI projects the same
  plan to the dashboard; per-card freshness uses twice the rotation interval.
  `clone_to_sd.sh` marks incomplete before writes and clears it only after a
  verified, flushed success stamp. Both cloud installers ship this whole cohort.
- Bootable clone evidence is built in `van_dashboard_hotspares.py`, using the
  existing normalized device-tree helpers in `van_dashboard_block_devices.py`.
  Root usage comes from mounted `lsblk` counters or a read-only, label-verified
  ext4 superblock probe. Never mount a spare merely to render its usage.
- Deal Watch recovery: `pi/scripts/price_check/search_watch/browser_refresh.py`
  owns the durable request queue and request contract. `browser_results.py` owns
  the bounded HTML submission/receipt contract; `browser_capture_limits.json`
  shares byte limits across hosts. Browser recovery records the captured page
  through `service.apply_search_results` without another eBay request or cookie
  replacement. `refresh_install.py` retains existing-header rechecks and the
  legacy explicit header installer. The bridge is
  `pi/scripts/price_check/ebay_refresh.py`; the Mac polling worker and managed
  clean-browser client are `macbook/scripts/deal_watch_refresh.py` and
  `macbook/scripts/ebay_browser_headers.mjs`. Browser session/verification code is
  `macbook/scripts/deal_watch_browser.mjs`; its anonymous cookies stay on the Mac.
  `pi/scripts/price_check/search_watch/browser_headers.json` remains the legacy
  header allowlist. Preserve Chromium headers without Firefox defaults. See
  `pi/docs/dashboard/PRICE_CHECK.md`.
  Recovery retries silently at six-minute intervals, stops after 50 consecutive
  failed attempts and sends one ntfy alert. A successful search resets the
  durable count; preserve claim fencing and idempotent failure acknowledgements.
- Deal Watch Haveno XMR offers: `pi/scripts/price_check/haveno_watch/` (`source.py`
  order book + reference price, `store.py` tables in the shared price-check DB,
  `service.py` matching/ntfy, `cli.py` `haveno-*` subcommands and `.haveno.lock`).
  `activity.py` runs market snapshots and classification; `activity_store.py` owns
  the inferred-offer activity tables and summaries.
  Order book is haveno.markets level-2 `asks` (RetoSwap only); reference is the Haveno
  pricenode with Kraken fallback, never the haveno.markets ticker. Each offer ID
  notifies once per watch. Its own 10-minute `haveno-check` cron line sits beside the
  price-check line. React: `DealWatchHavenoSection.tsx`, `havenoDecoders.ts`;
  activity subsection `DealWatchHavenoActivity.tsx`, `havenoActivityDecoders.ts`.

Some ignored secret directories, wireless profiles, and device configurations
may contain credentials or private network data. Never print or commit secrets,
and preserve unrelated local or deployed changes.

- Backup preferences: `pi/scripts/backup/backup_setting_fields.py` is the canonical
  editable-field schema (bounds, units and tooltip text); `backup_settings.py` owns
  revision-checked overrides in root-private `/etc/vanpi-backup-settings.json`.
  `backup_settings_control.py` is the fixed CLI. Workers apply preferences at the
  next run; native credentials and configuration remain separate. The shell helper
  emits only fixed numeric setting names, never shell code. `backup_schedule.py`
  migrates only the two owned cron entries to hourly `--scheduled` checks, retaining
  the prior root crontab; the configured Pi-time window gates new starts only.
  Dashboard `backup_settings_control.py` / `routes/backup_settings.py` and
  `features/backups/BackupSettingsPanel.tsx` own the settings subpanel;
  `useBackupPreferences.ts` owns per-setting autosave above the dialog lifecycle:
  switches/selectors save on selection, numbers on blur/Enter, with confirmed toasts.
  `settingsDraft.ts` validates input before revision-checked writes. Settings
  conflicts cross the privileged CLI as `EX_TEMPFAIL` and map to HTTP 409;
  human error wording is never used to select the response status.
- `pi/scripts/backup/cloud_backup_requests.py` owns durable `backup-now.json`
  requests. Back up now bypasses only the weekly cadence, coalesces repeated clicks,
  resumes an unfinished generation and is satisfied by one new verified generation.
  It never rewrites success history, relaxes admission or discards checkpoints.
- Dashboard backup configuration parsing lives in `pi/apps/van_dashboard/backup_configuration.py`;
  `features/backups/CloneCardCapacity.tsx` owns the reusable card-capacity control.

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

- Hardware: two USBCANFD adapters (the serial-resolved dual-USBCANFD
  installation) on C-CAN (500 kbit/s), B-CAN (125 kbit/s) and CAN CH
  (500 kbit/s), listen-only by default. `canN` names are not stable
  identities; its README's "Current dual-USBCANFD operation" is the source.
  The earlier PCAN-USB and `bringup.sh` are historical.
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

## Repository test suite

Run `.local/test-venv/bin/python run_tests.py` from the repository root before committing or reporting
completion. It discovers tracked and unignored new tests, excludes deleted files,
and runs isolated Python unittest modules per file, Node tests, shell tests,
Claude mod tests and the dashboard's Vitest suite. It stops at the first failed command and exits nonzero; omitted
remaining suites are not a pass. Missing interpreters/dependencies are errors.
Tests needing specific OS features or hardware must state their skip reason.
Set `BTT_LIVE_TESTS=1` to also enable BTT's opt-in native bridge tests.
Use an interpreter with Flask and Werkzeug installed (this checkout has an isolated
`.local/test-venv/bin/python`) and install the dashboard's npm dependencies first.
`run_tests.py` owns the standalone-script import roots in `PYTHON_IMPORT_ROOTS`;
extend that registry instead of adding per-test `sys.path` patches.

## Deployment commits

- After a successful deployment from this repository, commit and push the
  deployed repository changes once validation passes. Keep unrelated worktree
  changes unstaged and out of that commit.

## Hosted project directory

The Pi dashboard's StateStore key `hosted_projects` is the only source of the
title-menu project list; there is no static React catalog. Contract, schema
and reconciliation rules: `pi/docs/dashboard/HOSTED_PROJECTS.md`. The store
lives in `van_dashboard_projects.py` (`runtime.hosted_projects`, initialized
immediately after the state store), with report validation and shared
vocabulary in `hosted_project_report.py` and the one-time seed in
`hosted_project_seed.py`; the `projects` blueprint owns
`/api/hosted-projects*`. `hosted_project_scan.py` is the stdlib-only scanner
for all three hosts (Pi cron every 15 minutes, Mac LaunchAgent
`com.jacobr.hosted-project-scan` hourly over `vanpi.lan` only); scans report
only listeners whose page answers HTTP 200, and Pi links follow each unit's
bind address (the Pi scan covers lingering `systemctl --user` units too, so
LLM Switchboard, bound to Tailscale only, gets no LAN link). The Pi cron line is a managed
block in both the live `pi` crontab and `pi/crontab_pi`, but the repo's
crontab files lag the live ones in unrelated lines: run `pi/pull_crontabs.sh`
and review the diff before any `pi/push_crontabs.sh`. Preserve the
runtime state during deployment. Links are HTTP/HTTPS only and are never
fetched by the backend; preference is local, LAN, Tailscale, then web.

## Dashboard response schemas

The React frontend's USB, network and storage response schemas live in each
feature's `schema.ts`, with shared primitives and the only Valibot parsing
boundary in `pi/apps/van_dashboard/frontend/src/api/schema.ts`. Decoding fails
fast with a `TypeError`. Response types are inferred from schemas, while UI-only
computed types remain presentation types. Other features retain their decoders
where staged schemas add no real savings.

The production boundary is `Response.json()`; sparse JS arrays, accessors and
Proxies are outside that wire contract. Native arrays do not preserve hole
semantics from the old `.map()` decoders.

## Backup and disk tooling

The active design is represented by:

- `pi/scripts/backup/pi_backup.sh`: backup orchestration.
- `pi/scripts/backup/openwrt_backup.sh`: verified router exports; independent
  hourly cron checks use `--scheduled` within the configured daily window;
  Borg can also refresh before archiving.
  A directory flock serializes exports without taking the cloud/disk backup lock.
- `pi/scripts/backup/backup_watchdog.sh`: freshness and health monitoring.
- `pi/scripts/backup/rootfs_budget.sh`: bounded rootfs path budgets and daily growth history for the watchdog.
- `pi/scripts/rootfs_tidy.sh`: weekly apt-cache cleanup that runs the shared Codex release retention as root with `--owner pi --package all`.
- `shared/sh/cleanup_codex_releases.py`: Codex release retention for both the MacBook (weekly user cron via the `macbook/scripts/` symlink) and vanpi. Defaults to standalone; `--package all` checks standalone and app-server-daemon independently, each keeping three versions plus current/running releases under its own installer lock. An absent package is optional in an all-packages sweep; unsafe or incomplete installed trees fail.
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

Both installers print a private `<stage>/previous` directory. Older flat
`previous/` directories retain their saved install/mv recovery procedure.
`previous/tree/`
mirrors absolute destinations (for example `tree/home/pi/scripts/backup/` and
`tree/etc/systemd/system/`). `previous/manifest` has one pipe-delimited record
per plan entry: source, destination, old SHA-256 or `absent`, old owner:group,
old mode, staged SHA-256, staged owner:group, staged mode, replace/keep action,
and temporary path. All originals and replacements are prepared before the
first publication. `transaction/published` records attempted destinations;
`transaction/active-timers` records which timers were active before the fence.

Ordinary failures automatically restore attempted replacements in reverse
order while fd 9 still holds the exclusive legacy fence. Cleanup ignores repeated
INT/TERM/HUP until it ends, so a second stop cannot cut rollback short. It checks
both content and owner/mode, removes only verified newly introduced files,
and reloads restored systemd units before restarting any prior active timer.
An incomplete restore/reload prints `ROLLBACK INCOMPLETE`, exits nonzero and
leaves timers stopped. The rollback directory is retained. Activation actions
(including Time Machine capture requests) occur only after the cohort commits;
their failure reports a committed cohort with incomplete activation.

For a later manual rollback, first pause both cloud jobs with the existing
controls, preserve independent holds and let local jobs finish. Inspect the
saved manifest, private tree and current hashes before writing. Use the staged
`pi/backup_local_deploy.sh` contract, not a newer installed helper, and source
the staged `pi/scripts/backup/backup_locks.sh`. Validate and open the existing
legacy inode with `backup_lock_open`, acquire its exclusive
flock and stop the four cloud timers. Set `LOCAL_BACKUP_STAGE` to the saved
stage, initialize `LOCAL_BACKUP_TEMPORARIES=()`, and load the reviewed manifest
records into `LOCAL_BACKUP_PUBLISHED`. `local_backup_rollback` restores those
records and reloads units; `local_backup_cleanup_temporaries` removes its
staging files. Do not restart timers or release the fence before restoration
is verified. Restore only the previously active timers, then resume the
selected cloud job through its control entry point. There is no separate
standalone rollback executable.

Never remove or replace `/run/lock` inodes during recovery. The tmpfiles
configuration is in the cohort; created registry inodes remain in place.
With `L=/home/pi/scripts/backup`, verify with
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
