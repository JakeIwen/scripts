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
| `macbook/metered/mac-metered` | Mac shell, updater admission and Mac Panel | `macbook/metered/`; launchd uses its `-m macbook.metered tick` boundary |
| `macbook/scripts/display_cab_dimmer.py` | `com.jacobr.display-cab-dimmer` LaunchAgent and Mac Panel automation | `macbook/scripts/cab_dimmer/` plus the stable entry script |
| `macbook/scripts/amphetamine_control.py` | Mac Panel Amphetamine tab and Terminal | `macbook/scripts/amphetamine/` |
| `macbook/scripts/deferred.zsh` | `com.jacobr.deferred-jobs` LaunchAgent and Mac Panel | the script and `shared/automations/` declaration |
| `macbook/scripts/van_lighting.py` | BTT Media > Lighting: Solder SW (`toggle switch.solder_flood`) and the Lighting Colour web view (`lights`, `set`, JSON) | the script; `macbook/bettertouchtool/install_lighting_menu.py` installs both callers |
| `macbook/scripts/llm_photo_inbox.py` | iPhone Shortcuts Run Script over SSH (raw photo on stdin) | standalone stdlib Python 3.9+; macOS `sips`, `ps`, `pgrep`, `osascript` and running iTerm2 |
| `pi/configs/labwc/` | labwc/swayidle at `/home/pi/.config/labwc/`; castit's mpv lifetime contract | copied by `pi/sync_scripts.sh` |
| `pi.apps.metered`, `shared.metered`, `pi.package_runtime`, `pi.apps.media_organizer.{config,records}` in the package release, and the `-m pi.apps.media_organizer` / `-m pi.apps.iptorrents_watch` CLIs | `van-dashboard.service`, deployed from `~/dev/van-dashboard` | `pi/deploy_python.py` `MODULE_DIRS`; `pi/tests/test_python_deployment_consumers.py` pins them (see [Van dashboard](#van-dashboard-separate-repo)) |
| `pi.apps.video_library` | `video-library.service` and `pi/deploy_python.py` | `pi/apps/video_library/` |
| `pi.apps.audiobooks` | `audiobooks.service` and `pi/deploy_python.py` | `pi/apps/audiobooks/` |
| `pi.apps.bme280` | `bme280-mqtt.service` and `pi/deploy_python.py` | `pi/apps/bme280/` |
| `pi/scripts/vanpi-metered` | Pi shell, authenticated Mac SSH sync and cloud admission | `pi/apps/metered/` plus `shared/metered/` in the immutable package release |
| `pi.apps.metered` | `vanpi-metered.service`, independently of the dashboard | `pi/apps/metered/`; unit/package owned by `pi/deploy_python.py` |
| `pi/scripts/system_event_monitor.py` | system-event-monitor, crash-capture and network-recorder consumers | `pi/scripts/system_monitor/` plus the stable shim |
| `pi/sync_scripts.sh` | Mac non-backup broad sync | `pi/backup_sync_client.py`, `pi/scripts/update_services.sh`, `pi/scripts/backup/backup_deploy_sync.py` |
| `pi/scripts/backup/` | backup units, timers, cron and scoped deployers | the feature folder and `pi/backup_local_deploy.sh` |
| `pi/scripts/backup/offsite_backup.sh` (`pause` / `resume --remote`) | van-dashboard guarded backup controls and root CLI | `pi/scripts/backup/offsite_control.py`; revisions from `dashboard` |
| `/usr/local/lib/vanpi-backup-deploy/recover` | independent root cron `/etc/cron.d/vanpi-backup-deploy` | `pi/backup_deploy_bootstrap.py` and `pi/scripts/backup/backup_bootstrap_*.py`; outside cohort rollback |
| `pi/scripts/smart_watch.sh` | `vanpi-smart-watch.timer`; alerts on the `NTFY_WARNING_URL` topic | the script; see `pi/docs/monitoring/SMART.md` |
| `pi/scripts/alias_media.sh` | Pi `am`/`an` functions and qBittorrent's completion command (`alias_media.sh new`) | thin wrapper; `pi/apps/media_organizer/alias_main.py` in the package release |
| `python3 -P -m pi.apps.media_organizer mirror-sync` | `pi/scripts/backup/pi_backup.sh`, as `pi`, right before the media rsync | `pi/apps/media_organizer/mirror_sync.py` in the package release |
| `/run/lock/media-mirror.lock` | `pi_backup.sh` holds it exclusively for the whole media rsync | organizer moves hold it shared (`locking.backup_sync_excluded`; path in its `config.py`) |
| `macbook/scripts/sns.sh` | Mac shell alias and BetterTouchTool | `shared/python/sonos_cli.py` and `sonos_tasks.py` |
| `pi/sns.sh` | Pi shell alias and video audio checks (`/home/pi/sns.sh`) | shared Sonos CLI and tasks; package release imports |
| `shared/sh/` | Mac/Pi aliases, cron and targeted flat Pi deployment | feature-named scripts in the documented compatibility folder |
| `shared/codex_autocontinue/run` | Mac `~/.zshrc` `_codex_tui`; Pi copy at `~/.local/share/codex-autocontinue/` via `pi/.bashrc` | `shared/codex_autocontinue/` and its `deploy_pi.zsh` |
| `shared/codex_autocontinue/retirement-check` | weekly `macbook/crontab` job | `shared/codex_autocontinue/` |
| `pi/projects/phone-drop/` | `phone-drop.service` and its scoped deployer | the project folder |
| `ubnt/persistent/scripts/` | airOS boot hooks and `ubnt/scp_to_device.sh` | the complete Wi-Fi-manager cohort |
| `/volume1/vanpi-offsite/receiver` | DSM Task Scheduler `python3 -B -m receiver boot` from the private install root | `synology/offsite/`; explicit install, no systemd assumption |

## Repository map

- `pi/`: Raspberry Pi apps, backup, disk, service, deployment, and setup tooling;
  `pi/README.md` is the documentation index.
- `shared/metered/`: Python 3.9+ wire records, exact-SSID rules, consumer registry
  and locked revision/receipt stores. `records.py` owns wire vocabulary;
  `consumers.json` is the only consumer-id/host registry. `config.py` owns
  first-run identities; logic never hardcodes an SSID. `store.py` reuses
  `shared/python/tool_settings.write_private_json` and adds transaction locking
  and directory durability. Offline sync only adds SSIDs; receipts are retained
  rather than evicted, and exhausted capacity rejects new operations.
- `pi/apps/metered/`: Python 3.11 Pi controller, independent 10-second reconciler,
  CLI, strict cloud admission and qBittorrent hold adapters. `config.py` owns
  paths/deadlines; `controller.py` owns serialized host episodes and HostState;
  `torrent_api.py` reuses the video library's bounded HTTP transport;
  `torrents.py` respects disk/ignition policy and falls back to the existing
  graceful process stop. Remove `metered-hold` from a stopped torrent to keep
  it manually stopped after network recovery. The flat `vanpi-metered` shell
  entry imports the package release; `metered_shell.bash` supplies Pi aliases.
  `connectivity_status.collect_ubnt_association` is shared by dashboard and cloud
  probes; verified identity requires driver/target agreement and association.
  Cloud workers call the short-lived `admit` CLI so their long runs do not pin
  package releases. Pi approvals are host-local; the dashboard only displays
  reported Mac state; its `routes/metered.py` (van-dashboard repo) owns the JSON
  endpoints behind the dashboard's origin/header checks. Deployment and wire contract:
  `pi/docs/networking/METERED_CONNECTIONS.md`.
- `pi/scripts/rball.sh`: `s rball` reboots antenna, router, then the Pi through `safe_reboot.sh`; `--dry-run` checks uptime without rebooting.
- `pi/apps/audiobooks/catalog.py` owns audiobook records, filesystem discovery
  helpers and fuzzy matching rules; `audiobook_server.py` retains state, Sonos,
  routes, polling and the stable service entry behavior. The exact root page is
  packaged from `pi/apps/audiobooks/templates/audiobooks.html`.
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
- Van TV (YouTube cast receiver `youtube-cast.service`, DIAL port 8794, LAN/loopback
  status port 8795; its Firefox extension; the Mac `vantv` CLI) is maintained
  separately in `~/dev/castit/youtube/` (public repo
  `https://github.com/JakeIwen/castit`). Its `receiver/deploy.sh` owns
  `/home/pi/youtube-cast/` and the unit; never restore a copy here. Install
  addresses and integrations live in castit's ignored `youtube/local.json`.
  `pi/.bashrc`'s `vantv_*` functions route the BTT media buttons' ssh commands
  (`vlcmd`, `pp`, `play_status`, `kill_media`) to Van TV while a cast is loaded,
  and `pi/crontab_pi` runs the installed controller's weekly `update-ytdlp`.
- `pi/configs/labwc/`: labwc's idle-blanking autostart and hook, copied to
  `/home/pi/.config/labwc/` by `pi/sync_scripts.sh`. The hook keeps the screen
  on while VLC or mpv exists; castit's receiver relies on the `pgrep -x mpv`
  check. See `pi/docs/deployment.md#labwc-idle-blanking`.
- `pi/apps/media_organizer/`: the Movies & TV organizer (package release, Python
  3.11, stdlib). `records.py` is the wire contract (the van-dashboard repo's
  `features/mediaOrganizer/schema.ts` mirrors it and its backend imports
  `records`/`config`), `config.py` the paths.
  `scan.py` joins `torrent/New` to qBittorrent torrents; `classify.py` asks
  Codex on the Pi (`codex exec` with an output schema, one batch call) with an
  offline SxxEyy fallback; `plans.py` stores proposals and reviewer decisions
  (a partial plan stays editable for its open rows); `apply.py`/`relocation.py`
  move tracked payloads through qBittorrent set-location (never set-category),
  rename untracked ones, mirror the rename into `/mnt/bigboi/mp_backup` (the
  nightly rsync has no move detection) and journal each item write-ahead.
  bigboi is mounted only during the backup window, so a rename it misses is
  marked `mirror_state: pending` in the run journal and `mirror_sync.py`
  replays it when `pi_backup.sh` calls `mirror-sync` before its rsync (a miss
  costs a full recopy of the title; a failed replay only that). No move
  overlaps that rsync: the backup holds `/run/lock/media-mirror.lock`
  exclusively, interactive apply/undo refuse and the completion hook waits.
  A tracked folder holding files its torrent doesn't own (qBittorrent moves
  file by file and would leave them, e.g. Finder `._` files) is refused;
  `undo.py` replays a run backwards only when every payload is still where the
  journal left it. `cli.py` (`python3 -P -m pi.apps.media_organizer`) is the
  one entry point for the dashboard, the shell and the completion hook
  (`post-download`); all state lives in `~/.local/share/media-organizer/`.
  Apply, undo and the link rebuild share `/run/lock/alias-media.lock`.
  `alias_names.py`/`alias_links.py`/`alias_main.py` are the Python form of the
  old `alias_media.sh` link builder (byte-identical cleaned names; the fixture
  `pi/tests/media/alias_names_fixture.json` holds 222 real names);
  `VAN_ALIAS_OUTPUT_ROOT` builds into a scratch root for comparisons.
  The dashboard's Movies & TV panel runs this CLI (van-dashboard repo).
- `pi/apps/iptorrents_watch/`: the IPTorrents watcher (package release, stdlib).
  `records.py` is the wire contract (mirrored by `features/iptWatch/schema.ts`),
  `config.py` the paths. Login is the `uid`/`pass` cookies in
  `~/.config/jacobr/iptorrents.json` (mode 600, seeded from Firefox; never
  logged). `wantlist.py` derives wanted shows from the video library
  (`/api/library` shows played within 90 days or up next, newest episode on
  disk from `/api/shows/<id>`) plus manual entries; `parser.py` reads the
  site's `#torrents` table and fails loudly on a redesign; `filters.py` keeps
  1080p x264/H.264 TV releases newer than what is on disk and not already in
  qBittorrent or `~/tfiles`; `check.py` searches one spaced request per show
  with a 6-hour cache (Pi cron every 6 h, managed block in `pi/crontab_pi`);
  `download.py` writes a validated `.torrent` to `/home/pi/tfiles` only on an
  explicit `download <id>` (qBittorrent's watch folder takes it from there, and
  the media organizer files it when it finishes). State in
  `~/.local/share/iptorrents-watch/`. The dashboard's "Watchlist" sheet under
  the Movies & TV tile runs this CLI (van-dashboard repo).
- `vanrouter/`: reviewed OpenWrt persistence and backup-export artifacts.
- `ubnt/`: directional wireless-device profiles and uplink scripts.
  `ubnt/persistent/scripts/wifi_manager.sh` owns initialization and command
  dispatch; its flat `wifi_manager_*.sh` siblings contain function definitions:
  `runtime` (logging, locks, failures/cooldowns), `profiles` (inspection, admin
  login, editing/persistence), `scanning` (surveys, channels, automatic selection),
  `connect` (link checks, reloads/recovery), `transitions` (GUI/manual holds),
  `provision` (new networks), `status` (status/dashboard output), and `starlink`
  (power-off roaming). Ship the entrypoint and every sibling together.
- `macbook/shortcuts/`: stdlib Python 3.9+ SSH-key Shortcut specs and builders: Pi command shortcuts (`pi_shortcuts.py`, `workflows.py`) and the "To Mac LLM" photo share sheet (`mac_shortcuts.py`, `mac_workflows.py`); shared plist helpers live in `workflows.py`. From the repo root run `/usr/bin/python3 -m macbook.shortcuts.build_shortcuts` to build both sets (requires macOS `/usr/bin/shortcuts` signing); outputs are ignored under `macbook/build/shortcuts/`.
- `macbook/scripts/van_dashboard_http.py` is the Mac's one JSON client for the Pi
  dashboard API (`DASHBOARD_URL`, ok-checked GET and form POST); the cab dimmer and
  `van_lighting.py` use it.
- `macbook/scripts/van_lighting.py` drives dashboard lights: `toggle <entity>` flips a
  light or power switch (state from `/api/lights`, write through `/api/lights/power`;
  failures also show a macOS notification), and `lights`/`set` print JSON for the
  colour control. BTT's Media > Lighting submenu (💡, last on the bar's second row)
  holds Solder SW and 🎨, which shows the top-level "Lighting Colour" dropdown: a Web
  View built from `macbook/bettertouchtool/lighting_control/` (reusable
  `LightControl.mount`, iro.js wheel, runs `van_lighting.py` through BTT's
  `runShellScript`; see its README). `install_lighting_menu.py/.js` beside it installs
  all of it (inspect by default, `--apply`).
- `macbook/metered/`: Python 3.9+ Mac controller and stable `mac-metered` CLI;
  `com.jacobr.metered` runs once per minute in the GUI login session, not as a
  daemon. `wifi_helper/reader.py` reads fresh Location-authorized CoreWLAN
  identities through its app; `network.py` reads gateway/ARP, `sync.py` owns
  bounded SSH and the restricting-only offline queue, `decisions.py` leases
  cached permissions, and `low_data_mode.py` owns constrained-flag records.
  State/config cache is `~/.local/state/metered/`; host-local policy is
  `~/.config/jacobr/metered.json`. `shared/settings/metered-mac.json` declares
  the separate opt-in flag; `tool_settings` writes it. Low Data Mode clears
  only a flag this job set and has seen stay set (ownership survives sleep and
  network changes); flags set without its record are native and left alone. `mac_panel/metered_adapter.py` and
  `static/section_metered.mjs` expose the CLI without bypassing request guards.
  Install/test commands and lease limitations: `pi/docs/networking/METERED_CONNECTIONS.md`.
- `macbook/linux_test_lane/`: the opt-in Linux test lane (Python 3.9+, CLI
  `macbook/linux_test_lane/linux-test-lane`), a Lima `vz` Debian 12 arm64 VM
  on the Mac that runs the Linux/Pi tests off the Pi. `pins.env` is the one
  record of the image (dated URL + digest), Pi-matching tool versions and VM
  size; `provision.sh` runs as root in the VM and refuses drift from those
  versions (restic comes from `pi/scripts/backup/install_restic.sh`, rest-server
  from `synology/offsite/binaries.py`, reusing their verified release pins);
  `requirements.txt` mirrors `.local/test-venv`. `lima.py` renders the template
  and wraps `limactl`; `sync.py` copies exactly the files `run_tests.py`
  discovers, plus Git state (a worktree's own HEAD/index), through a read-only
  mount of the main checkout onto VM-local `/home/pi/linux-test-lane/checkout`,
  keeping the VM's own venv and `.git/run_tests/` cache. Usage under
  [Repository test suite](#repository-test-suite).
- `macbook/`: macOS shell utilities, AppleScripts, and BetterTouchTool helpers.
  `macbook/scripts/shimg.zsh` is called by `~/.zshrc`'s `shimg` function;
  it saves clipboard PNGs into the Pi `agent-shared` folder under
  lowercase-hyphen names and never touches the clipboard. By default it
  drops `screenshot-pending-N.png` and a detached job renames it from a
  Haiku or GPT-6 Luna glance (order from the lean in the usage budget's
  `report.txt`; `screenshot-N` if both fail); `-f` names it `screenshot-N`
  at once; a given name replaces its file. When pihome isn't mounted it
  mounts it quietly (AppleScript `mount volume`) and opens the folder in
  Finder, which focuses a window already showing it.
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
  share read-only inspection and backup helpers; `btt_socket.py/.js` call BTT's
  CLI over its local socket (reuse them instead of another `bttcli` wrapper).
  `repair_media_actions.py`
  defines the action-recovery wire contract consumed by its JXA worker.
  `macbook/bettertouchtool/btt_guard/` owns explicit known-good checkpoints,
  read-only integrity audits, approved restoration, ntfy warnings and periodic
  launchd control. Its `model.py` owns shared contracts/statuses; `storage.py`
  owns private atomic state. Besides the Media graph it watches every top-level
  trigger of `database.WATCHED_TRIGGER_TYPES` in active presets (presence and state
  only; a new one is `added` drift). Never promote audit output during ordinary audits
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
  `sonos_commands.py` supplies the reviewed Sonos caller plan to
  `repair_script_paths.py --sonos`: normalize paths/shell invocation, validate all
  arguments offline and preserve explicit remote intent and named-trigger links.
  It reuses the existing guarded field updater and saved-state verification.
  Path updates preserve exported enabled flags explicitly (BTT may enable a
  disabled button when they are omitted). `btt_common.records` includes
  `ZENABLEDNEW` so saved-state checks cover activation.
  `restore_path_enabled.py` recovers only 0-to-1 enabled drift against a path
  repair backup, preserving completed command edits and refusing other drift.
  `repair_key_sequences.py` (`btt.py repair key-sequences`) restores Master key
  sequences lost from the current store using BTT's older per-version stores, and
  lists triggers linked to both Trash and Global, which a Trash purge deletes.
  `repair_text_transforms.py/.js` owns the guarded text-transform installer;
  `text_selection.js` owns AX selection handling and `text_menu_paste.js` owns
  clipboard-preserving native Paste for editors with broken AX replacement.
  `text_markers.js` preserves Firefox text-node positions during reselection;
  never round-trip rich-editor selection endpoints through flat offsets.
  `install_text_editing_keys.py` (`btt.py text-keys`) owns the system-wide editing
  keys (Cmd-X line cut, Cmd-C line copy, quote/bracket
  wrapping): conditional pass-through triggers that fire only on a settable
  focused selection outside `EXCLUDED_APPS`; `text_editing_conditions.py` owns
  those conditions and the truth table checked through `text_editing_conditions.js`
  before install, and `text_editing_keys.js` is their runtime on top of
  `text_selection.js`. A line is the caret's logical line with its break
  (numeric range over `AXValue`; Firefox's marker paragraph). Cmd-X selects it
  through Accessibility only while the caret is as first read, acts only on an
  exact readback with the text unchanged and focus checked last, and presses
  Edit > Cut through Accessibility, since BTT swallows a Cmd-X sent from its own
  trigger; Firefox blank lines are not cut. Cmd-C never touches the field: it
  writes the line's text plus `\n` to the clipboard with `text_menu_paste.js`'s
  `writeClipboardText`, refusing if the caret, focus or clipboard changed while it
  read, and a failed write puts the previous clipboard back. Never select by
  posting keys: neither BTT's key actions nor `CGEventPost` confirm delivery.
  Their tests reuse `tests/firefox_editor_fixture.cjs`, the Firefox marker model
  shared with `text_markers.test.cjs`, and `tests/pasteboard_fixture.cjs`, the
  in-memory pasteboard shared with `menu_paste.test.cjs`.
  `install_finder_paste.py` (`btt.py finder-paste`) owns Cmd-V in Finder: one global
  conditional pass-through trigger (Finder frontmost, no settable text focus) whose
  runtime `finder_paste.js` presses Finder's Edit > Paste for files and text and
  saves a clipboard image as a PNG in Finder's insertion location;
  `finder_paste_condition.py` owns its condition and truth table. BTT's
  `add_new_trigger` files new triggers under Global whatever their
  `BTTAppBundleIdentifier`, so the condition does the app scoping.
  `trigger_install.py` owns the shared guarded install of fixed-UUID top-level
  triggers (create disabled, verify readback and saved store, enable; a failure
  leaves them disabled). `tests/btt_socket_fake.py` is the in-memory BTT socket for
  installer tests.
  `signal_text_diagnostic.js` owns the temporary Signal diagnostic button's runner.
  `macbook/crontab` is the tracked user crontab; `macbook/tests/test_crontab.py`
  checks that every repo script it runs (paused `#off ` jobs included) exists and
  is executable. `macbook/scripts/crontab_sync.py` owns its parsing, stable job
  ids, `#off ` pauses, revision-checked writes, drift-safe install (refused while
  the live crontab has lines the file lacks) and private live backups in
  `~/.local/state/crontab-sync/`; llm-restore keeps its own switch.
  `macbook/scripts/time_machine_exclusions.sh` (hourly cron) re-stamps Time
  Machine's sticky exclusion on `node_modules` under `~/ais` and `~/dev` and on
  its fixed list of reinstallable folders; add new exclusions to that list.
  `macbook/scripts/deferred.zsh` owns one-off future jobs on the Mac (queue,
  hourly/login checker LaunchAgent `com.jacobr.deferred-jobs`, purge on success,
  `NTFY_WARNING_URL` on failure, daily cron watchdog); queue jobs with its `add`
  instead of writing a LaunchAgent per job. Usage is in `macbook/README.md`.
  Its `list --json`, `retry`, `run-now` and `reschedule` serve the Mac Panel;
  every mutation (and `cancel`) takes the checker's own flock and exits 75 when
  it is busy, and `run-now` never sends a failure alert.
  LaunchAgent plists name `/usr/bin/python3`, a versioned Homebrew interpreter
  or a venv, never `/opt/homebrew/bin/python3`: Homebrew repoints that link on
  each Python install, and macOS re-posts its background-activity notice for
  every job using it (`macbook/tests/test_launchagent_interpreters.py`).
  `macbook/launchagents/com.jacobr.dictation-key-f13.plist` remaps the F5
  dictation key to F13 with `hidutil` at login; castit's Van TV extension binds
  F13 to its sidebar toggle. Setting `UserKeyMapping` replaces the whole list,
  so any other key remap joins that plist rather than getting its own agent.
  `macbook/mac_panel/` is the Mac Panel, `http://127.0.0.1:8769/` (port 8769;
  `macpanel` alias in `~/.zshrc`; BTT menu-bar icon and ctrl+opt+cmd+P from
  `bettertouchtool/install_mac_panel_menu.py`): a stdlib server run by the login
  LaunchAgent `com.jacobr.mac-panel` (gui domain, venv
  `~/.local/share/mac-panel/venv`; `install.zsh`, controller `panel_service.py`
  with prepare/install/status/start/stop/restart/verify/uninstall and
  `sudoers --print|--script`; runtime files in ignored `tmp/mac-panel/`;
  `MAC_PANEL_PORT` and `MAC_PANEL_STATE_DIR` run a second instance for
  development). One adapter module per section, wire records in
  `panel_contracts.py`, request guards (Host allowlist, per-instance token,
  Origin + JSON on POST) in `request_guard.py`, `wire_validation.py` (every
  armed POST needs `confirmed: true`, else 400), the only subprocess boundary in
  `cli_runner.py`, display redaction in `log_redaction.py`. It changes tools only
  through their own CLIs or `tool_settings` (crontab edits and run-now go
  through `crontab_sync.py`, deferred add through `deferred.zsh add`); ntfy
  config is read-only there, BTT repair is never exposed, and it never reads
  pi/secrets. Controls: `services_adapter.py` starts, stops and restarts
  LaunchAgents and restarts `com.jacobr.*` daemons through `sudo -n
  /bin/launchctl kickstart -k system/<label>`, allowed by the one sudoers line
  `sudoers_rule.py` renders (`/etc/sudoers.d/mac-panel-daemons`, installed by
  Jacob from `panel_service.py sudoers --script`); long actions (automation
  `async` actions such as codex-update `check-now`, cron run-now) run in
  `background_tasks.py` and are polled at `GET /api/tasks/<id>`. Its Automations
  section (`automations_adapter.py`) needs no per-tool panel code: it lists
  `shared/automations/<tool>.json` declarations, runs each one's status command
  and the declared actions by fixed argv. Its Logs section (`logs_adapter.py`,
  `static/section_logs.mjs`) relays `macbook/scripts/log_rotate.py` and reads sizes and paths only. `sampler.py` records each section's
  level and a few values into the panel-owned SQLite history
  `~/.local/state/mac-panel/history.sqlite` (`history_store.py`: per-series
  thinning, 30-day prune; `GET /api/history`), and `usage_adapter.py` serves
  `~/config/mac/usage-budget/usage_budget.py facts --json` (read-only cached
  facts, no account identity) at `GET /api/usage`. Its page is ES modules in
  `static/` with no build step: `app.mjs` (`MODULES` table, polling, panel
  health), `shell.mjs` (Overview-first grouped sidebar nav, hashes, drawer below
  900 px), `search.mjs`/`search_ui.mjs`/`search_actions.mjs` (Cmd-K search over
  sections, settings fields, jobs, services, consumers and actions; a search
  action only navigates and arms a button), `panel_sections.mjs` (state
  registry, `refresh`/`action` and the dirty check polls obey), `panel_api.mjs`
  (the only `fetch`), `dom_core.mjs`/`components.mjs`/`panel_dom.mjs` (armed
  buttons, `card`, `kv`, `settingRow`, `toggle`, `info` icons with tap popovers,
  notices, times), `charts.mjs` (inline-SVG `stateStrip`, `sparkline`, `gauge`,
  `scheduleStrip`; geometry is Node-tested), `history_panel.mjs`,
  `task_receipt.mjs`, `status_cell.mjs` (`Level` vocabulary), `tokens.css`
  (dark theme from the van dashboard palette, light under `data-theme="light"`;
  no `style` attributes under the CSP, dynamic geometry through CSS custom
  properties), copy in `section_copy.mjs`, and one `section_<name>.mjs` per
  section exporting `render` and optional `searchItems`, `attentionItems`,
  `cardExtra`. `server.py` serves any lower-case `.mjs`/`.css` in `static/` at
  the root; `tests/test_static_page.py` checks each module parses and its
  imports resolve. Service log tails load on demand from
  `GET /api/services/logs/<label>`, and `/healthz` reports `source_changed` so
  the page offers a restart after edits to the panel's own Python.
  Usage: `macbook/mac_panel/README.md`.
  `amphetamine_adapter.py` and `static/section_amphetamine.mjs` expose native
  auto-lock settings through `macbook/scripts/amphetamine_control.py`.
  Its `amphetamine/` package owns the preference/session records (`model.py`),
  macOS commands (`native.py`), revision checks and rollback (`control.py`),
  and the CLI/caffeinate lifetime (`cli.py`). It runs on system Python 3.9.
  Native Amphetamine preferences remain authoritative; no polling agent or
  parallel settings file is installed. Changes preserve an indefinite open-lid
  session, refuse special sessions, and back up preferences under
  `~/.local/state/amphetamine-control/`. Enabling starts a keep-awake session;
  disabling auto-lock leaves an existing keep-awake session running.
  `macbook/scripts/ntfy_warning.py` is the one sender for `NTFY_WARNING_URL`
  warnings (sourced privately from `pi/secrets/.bash_variables`, never in argv
  or logs): btt_guard imports `send_warning`, deferred.zsh runs it with the
  message on stdin. Reuse it rather than reading the URL again.
  `macbook/scripts/firefox_quiet_prefs.py` owns the managed block of ad,
  recommendation, study and telemetry prefs in every active Firefox profile's
  `user.js` (release and Developer Edition); change prefs in its `PREFS` table.
- Custom Mac keyboard mappings: BTT shortcuts and `repair_key_sequences.py`,
  plus `macbook/launchagents/com.jacobr.dictation-key-f13.plist`, are
  documented in `~/dev/visual-guides/static/reference/keyboard-shortcuts.json`.
  The Keyboard Shortcuts guide and shell `keys` command share that catalog.
  When changing these mappings, update it in the same task, preserve scope,
  conditions and enabled state, and check `keys --all` plus `npm test` in
  Visual Guides. Keep private expansion text and executable payloads out
  of the public catalog; its verification date records an inventory check,
  not proof that the shortcut was exercised live.
- `shared/`: code imported or deployed on more than one host.
- `shared/codex_autocontinue/`: stdlib Python 3.9+ transparent PTY wrapper that
  types `continue` (with backoff) after an interactive Codex turn fails with
  "Selected model is at capacity". Stable launch: `shared/codex_autocontinue/run
  -- <command> <args>`; it preserves cwd and child environment, and non-TTY stdin
  uses direct exec. `pty_proxy.py` owns forwarding, `terminal_session.py` the tty,
  signals/window size/title stack, `detector.py` streamed turn detection, and
  `retry_state.py` cancellation and the attempt limit. Edit the delay formula (linear)
  in `backoff.py`; defaults live only in `shared/settings/codex-autocontinue.json`,
  with `tool_settings.Reloader` reading private overrides. Detection keys on Codex's
  own `OSC 7501;state=error|done` signal, which works inline (Mac, `--no-alt-screen`)
  and full-screen (Pi), where the renderer splits text with cursor moves; only a
  fresh `state=error` followed by the message triggers, so redraws never do. Sessions
  without that OSC fall back to inline line matching (and the `Worked for` line for
  completion). Only a completed turn resets the attempt count. `fake_server.py` is
  the loopback Responses fixture; `run --demo` runs a real TUI with short delays and
  an isolated temporary home/provider in embedded mode (no account, daemon
  download, or real Codex home). Tests include real-TUI PTY integration in both
  screen modes; only those skip if Codex is absent.
  `run` starts Python through `~/.local/state/codex-autocontinue/codex-retry`
  (a symlink) so iTerm tabs read `(codex-retry)`; never name it `codex`, which
  `llm_restore.py` would count as a TUI. The Codex child sits on the wrapper's
  pty, so `uncommitted_owners.py` finds its tab through ancestor ttys.
  Pi: `zsh shared/codex_autocontinue/deploy_pi.zsh [--test]` installs the wrapper
  (not the retirement check) over `vanpi.lan` into
  `~/.local/share/codex-autocontinue/` with repo paths, keeping `.previous` for
  rollback, and checks hashes; `pi/.bashrc`'s `_codex_tui` runs it. The Mac's
  `_codex_tui` (private `~/config`, `mac/shell/`, loaded by `~/.zshrc`) runs
  the checkout's `run` directly.
  `retirement-check` is the weekly Mac cron entry (`--dry-run` previews without
  mutations). `retirement_probe.py` tests installed Codex against the loopback
  Responses fixture with default retries and an isolated temporary HOME;
  PR #31058 is informational only. `retirement_state.py` owns records/decisions,
  `retirement_store.py` locked private atomic state, `retirement_actions.py`
  Mac/Pi settings and phone adapters, and `retirement_check.py` orchestration.
  State lives in `~/.local/state/codex-autocontinue/retirement.json`.
  A successful native retry disables both wrappers; failed Pi writes retry
  weekly. Notification claims precede delivery to prevent duplicates, so failed
  delivery is logged rather than retried. Shell-launcher cleanup remains manual.
- Sonos: `shared/python/sonos_cli.py` owns validated argv dispatch
  and read-only diagnostics; `sonos_cli_commands.py` owns command argument types,
  ranges and CLI-only signatures; `sonos_tasks.py` owns operations and lazy
  discovery. Mac `macbook/scripts/sns.sh` executes `~/py3env/bin/python` directly
  (`SNS_PYTHON` override), independent of shell activation; `--pi` explicitly
  uses SSH and never retries a failed local operation. Pi `pi/sns.sh` preserves
  pinned/rollback task imports and the exec-only `ensure_rear_movie` path; it
  never changes ALSA volume. Shared CLI supports Python 3.9+; verified runtimes
  are Mac venv 3.13.2/SoCo 0.30.10 and Pi 3.11.2/SoCo 0.30.4. Tests use fixture
  speakers only, never live discovery/playback/configuration. See
  `shared/README.md` for diagnostics and setup.
- Tool settings: each configurable tool declares its settings once in
  `shared/settings/<tool>.json` (name, type, range, default, description), or,
  for tools that live outside this repository, in
  `~/.config/jacobr/declarations/<tool>.json` (often a symlink; a tool declared
  in both places is an error; a dangling link is listed and its load error
  names the missing target; the panel turns any unloadable declaration into an
  error card for that tool alone);
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
  The stable script owns CLI/status wiring;
  `macbook/scripts/cab_dimmer/model.py` owns display records and plug-in rules,
  `state_store.py` owns locked private state, and `displays.py` owns lazy
  CoreGraphics enumeration so status-only commands do not import macOS bindings.
  `dashboard.py` is the HTTP adapter, `runtime.py` owns callback/poll ordering,
  and `status.py` renders the read-only automation contract.
- Automations: `shared/python/tool_automation.py` (stdlib, 3.9) is the one
  definition of a panel automation: the `shared/automations/<tool>.json`
  declaration (title, optional service label and settings tool, a status
  command and actions as argv with `{repo}`/`{home}` placeholders and
  timeouts of at most 120 s) and the status record a tool's status command
  prints (`status_record`; levels mirror `status_cell.mjs`'s `Level`). The
  panel only reads declarations and relays actions; each tool keeps its own
  durable state (the dimmer's is `~/.local/state/display-cab-dimmer/`).
- `shared/sh/session_search_header.sh` owns the search flags, shared shell launch
  and index activity-age/resume formatting. `session_search_extract.py` owns
  single-pass grep extraction, reads Codex titles once per search and emits the
  `SearchRecord` contract from `session_search_display.py`. `session_search_excerpt.awk`
  batches each file's original locale-sensitive excerpt/context windows; grep
  starts no jq processes. `session_search_forks.py` owns `-f`/`--forks`: it reads
  matching provider/id pairs (`extract --ids`, or the index's ids) and prints each
  one's fork tree from transcript heads (Claude `forkedFrom.sessionId` on copied
  rows, Codex `forked_from_id` in the first `session_meta`; Codex subagent threads
  are left out unless matched). `llmsearch`, `claudesearch` and `codexsearch`
  share these files: a change requested for `llmsearch` applies to the other
  two too unless it is about cross-provider merging or the index, and goes in
  the shared helpers (activity ages exist twice, in `session_search_header_at`
  and `session_search_extract.py`'s `header`; keep them in step), not a
  per-command branch. Keep all ten search files together on the Pi under
  `/home/pi/scripts/`; `pi/.bashrc` defines all three commands. Python helpers
  require 3.9+ and shells support Bash 3.2. Targeted updates ship the complete set
  and patch only those live `.bashrc` definitions, preserving local changes.
  `session_search_paths.py` lists transcripts by newest activity before content
  is searched, interleaving providers for `llmsearch`; excerpts within a session
  are newest first too. One result per session is the default (`-g`); `-e`/`--single` lists every
  excerpt (`-s` is `--session`, an id-prefix lookup that, like `-t`, skips the
  index). Ctrl+C: the Python helpers keep SIGINT's default action, and the header's
  INT trap prints `<command>: aborted` and exits 130. All three searches give one result per transcript with its match count and last
  matching excerpt. `session_search_display.py` incrementally groups `llmsearch -d`/`--dirs`
  by directory/tool and redraws terminal output; pipes get the final listing only.
  Records mode (`SESSION_SEARCH_RECORDS=1`) is never grouped. The last `-d`, `-g` or
  `-e` wins in `llmsearch`; sessions and directory groups are newest first. Directory counts span sessions, and the latest matching event selects the resume command (mtime
  fallback without timestamps). Headers are soft orange (256-colour 215) only
  on terminals with no nonempty `NO_COLOR`; records and redirected output
  remain plain.
  All three commands accept `-t`/`--title` (alias `-n`/`--name`) for literal,
  case-insensitive saved titles only, one hit per session with no directory fallback. `-N`, `-a N` and
  `-b N` select context inside a matching message; `--` ends flag parsing.
  `llmsearch` defaults to the same literal, case-insensitive substring scan as
  `claudesearch` and `codexsearch`, including thinking and tool output, so a
  partial word does not silently lose matches. `--index` opts into this machine's
  faster whole-word LLM Switchboard index of prompts and visible replies
  (`llm-switchboard search --json`); `session_search_records.jq` owns index
  normalization/grouping. `--grep` restores the default; the last mode flag wins.
  Title/session mode, non-default roots, records mode, a missing command or exit 3
  (no finished index build, asked twice) still use grep. The llm-switchboard repo's
  `tests/test_search_oracle.py` consumes these scripts' records mode, so preserve
  that format and the fallback triggers.
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
  Resume commands start as `codexp`/`claudep` would: Codex under the
  `codex_autocontinue` wrapper when installed, and `env -u DISPLAY` on the Pi.
  State and the generated restore script live in `~/.local/state/llm-restore/`.
- `shared/sh/uncommitted_owners.py` implements `+id`: it attributes dirty Git
  paths to recent Claude Code and Codex tool calls, rolls subagents up to their
  resumable parent, names open sessions' terminal location (and, for Claude,
  the `SendMessage` name from `~/.claude/sessions/<pid>.json`) and prints resume
  commands for closed sessions. It uses `llm_restore.py --list --json` for Codex
  open-session detection and degrades to unknown when that listing fails.
- `shared/sh/claude_session_title.py` (stdlib, Python 3.9+) reads a hook payload
  on stdin and prints the conversation name: latest custom title, agent name,
  then generated title, scanning at most 16 MiB backwards. The Mac/Pi
  `claude_ntfy_notify.py` hooks call this CLI (no shared import root) and lead
  the notification body with the name, capped at 60 characters for previews;
  failed/missing metadata keeps the original alert. `--send` is unchanged.
  Deploy the helper first, then `pi/scripts/claude_ntfy_notify.py`, both flat
  in `/home/pi/scripts/`; Mac hooks run directly from the checkout. Tests:
  `shared/tests/test_claude_ntfy_titles.py` includes the Pi's deployed layout.
- `shared/sh/plus_commands.py` prints Jacob's + commands: the first sentence of
  each bullet in the global instructions' "+ commands" section. With `--hook`
  it is the UserPromptSubmit hook (Claude and Codex, Mac and Pi) that answers a
  bare `+cmd` without a model turn and passes every other prompt through.
  After the + commands it lists the user's slash commands, which
  `shared/sh/slash_commands.py` finds without running anything: Claude
  command files in `~/.claude/commands/` and each mod's literal
  `$.command.register({...})` metadata (mods from `CLAUDE_CODE_PLUGIN_DIRS`),
  or Codex prompts when `--file` is under `~/.codex`; skills are left out.
  `uncommitted_owners.py`, `plus_commands.py` and `slash_commands.py` sit
  flat in `/home/pi/scripts/` on the Pi, next to `llm_restore.py`; deploy
  `slash_commands.py` first, since `plus_commands.py` imports it.
- `shared/sh/known_terms.py` keeps `~/.claude/KNOWN-TERMS.md`, the terms Jacob
  already knows, which the global "Teaching as you go" rule leaves out of
  **Terms** blocks. With `--hook` it is a SessionStart and UserPromptSubmit
  hook (Claude and Codex, Mac and Pi): `+known <terms>` records terms without
  a model turn, and every session gets the list as hook context, then only
  the terms added since (per-session state in `~/.claude/known-terms-seen/`).
  The Mac merges both machines' copies over `vanpi.lan` on each change; the
  Pi copy sits flat in `/home/pi/scripts/`; ship `peer_copy.py` first.
- `shared/sh/peer_copy.py` owns bounded, noninteractive SSH reads and atomic
  writes of home-relative text files for known terms and the glossary. It sits
  flat in `/home/pi/scripts/`; ship it before either importing script.
- `shared/sh/glossary.py` keeps taught definitions in `~/.agent/glossary/terms.jsonl`
  (stdlib, Python 3.9+). Its Claude/Codex Stop hook records **Terms** blocks;
  UserPromptSubmit `+terms [search]` merges the copies over `vanpi.lan`, renders
  `~/.agent/glossary/glossary.html` and blocks with the local page URL. CLI:
  `list` (default), `remove <terms>`, `render`, `sync`; `--peer ''` stays local.
  `glossary_page.py` renders the offline search/flashcards page and known badges
  from `known_terms.py`'s list. On the Pi all four files sit flat in
  `/home/pi/scripts/`: ship `peer_copy.py` first, then `known_terms.py`,
  `glossary_page.py` and `glossary.py` together. Pi page URLs use the Mac's
  `/Volumes/pihome` mount. Local read/replace transactions use `terms.jsonl.lock`.
  A repeated session+term fills in a page link the first record lacked (the
  long-reply guard's follow-up carries it), and merges keep the linked copy.
- `shared/sh/long_reply_guard.py` is the Claude/Codex Stop hook (Mac and Pi)
  behind the globals' "Long replies" rule: a reply Jacob reads in a terminal
  that runs past 450 words or 50 estimated rows at 104 columns, fenced blocks
  left out and with no `~/.agent/artifacts/` link, is blocked once with
  instructions to move it to a page. Claude sessions count as attended through
  `CLAUDE_CODE_SESSION_ATTENDED`; Codex through its rollout's `session_meta`
  source (`cli`, not `exec` or a subagent). Stdlib; the Pi copy sits flat in
  `/home/pi/scripts/`.
- `shared/sh/reply_links.py` implements `+links`: with `--hook` it is the
  UserPromptSubmit hook (Claude and Codex, Mac and Pi) that answers a bare
  `+links` without a model turn, listing each `file://` page and hosted
  claude.ai Artifact link from the session's own replies (Claude assistant
  rows outside sidechains, Codex assistant response items, and older Codex
  `[label](/path.html)` page links) once, oldest first, with time and page
  `<title>`; given a transcript path it prints the same list for any
  session. Pi URLs use the Mac's `/Volumes/pihome` mount. Stdlib; the Pi copy
  sits flat in `/home/pi/scripts/`.
- `shared/sh/notify_on_finish.py` is the Python 3.9+ Claude/Codex UserPromptSubmit
  and Stop hook behind `+n`: standalone tokens arm a per-session flag under
  `~/.agent/notify-on-finish/`, Stop consumes it once and starts the existing
  `claude_ntfy_notify.py --send` detached; flags expire after two days. `hold`
  skips one Stop while background work continues, retaining the armed flag.
  Mac hooks run from the checkout; deploy the standalone script flat to `/home/pi/scripts/`
  before installing Pi hooks. CLI tests: `shared/tests/test_notify_on_finish.py`.
- Shell tricks, Jacob's small interactive helper set (13 per host, at most 16):
  `shared/sh/shell_tricks_common.sh` (tools both hosts share, plus `tricks`),
  host files `macbook/scripts/shell_tricks.zsh` and `pi/scripts/shell_tricks.bash`,
  and their one help doc `shared/sh/shell_tricks.md` (a `## name` section per
  tool with `Hosts:`, `Summary:`, `Usage:`; `tricks [NAME|-l]` prints it). The
  Mac's `~/.zshrc` (tracked in the private `~/mac-config` as `zshrc`) and
  `pi/.bashrc` source the host file before any alias. The Pi runs all three flat
  from `/home/pi/scripts/`: ship them together. `shared/tests/test_shell_tricks.py`
  keeps tools and doc in step; behavior cases live in `shared/tests/shell_tricks_cases.sh`.
- `pi/scripts/nfn.py` is the Pi's `nfn NAME 'COMMAND'`: it writes a forwarding
  function, using `builtin` or `command` for a leading self-call. The `pi/.bashrc`
  wrapper reloads only when the rc inode changed; `nalias` is a migration stub.
  A clean Bash classifies names: command, builtin and keyword shadows go at the
  end of the shadowing-function block below the Codex boundary; other functions
  go in `### NFN ### (added by nfn)` immediately above it. One single-line
  definition is replaced or moved; aliases, multiline and duplicate definitions
  require a hand edit. Placement and parsing intentionally duplicate the Mac's
  private `~/mac-config/nfn.py` implementation.
  It commits the function edit to `pi/.bashrc` on GitHub master with `gh api`
  (contents-API compare-and-swap on the blob SHA), then installs it as
  `/home/pi/.bashrc`; when the live file and GitHub differ, or GitHub refuses,
  it aborts with both unchanged. The Mac checkout is then behind:
  `pi/sync_scripts.sh`'s `check_pi_bashrc_known` refuses to overwrite a live
  `.bashrc` that is no version of `pi/.bashrc` in the checkout, so pull first.
- Pi `codexp`/`codexpr` accept `-x` to disable approvals and the sandbox;
  `codexpm` accepts it before or after the model/effort pair. The `codexpm` and
  `claudepm` model shortcuts resolve the newest locally listed version by family
  initial (`g` means plain GPT). Shared implementation is owned by the private
  config checkout at `~/config/shared/model-selection/`, loaded on invocation;
  install that folder before deploying `pi/.bashrc`. Missing files fail only
  these launchers, not shell startup; this repo's tests need no private checkout.
- `pi/.bashrc` has a Codex boundary: `[[ -n ${CODEX_THREAD_ID-} && $- != *i* ]]
  && return`. Above it are commands, all functions; below are exports, secrets,
  `DISPLAY`, completion, prompt and shadowing functions (`grep`, `vi`, `pip3`).
  Only Codex shells stop there. Codex runs `bash -c` with no rc, and the Pi's
  `~/.codex/config.toml` sets `BASH_ENV=/home/pi/.bashrc` through
  `shell_environment_policy`. Interactive and ssh shells still load the whole
  file, so `ssh vanpi <command>` works. Write functions, not aliases (the one
  alias left is `sudo='sudo '`; `sudo <function>` can't work), and put a name
  that shadows a command or builtin below the line.
  `pi/tests/test_bashrc_codex.sh` loads the file in Codex mode and checks it.
- `shared/sh/destructive_command_guard.py`: PreToolUse hook for Claude/Codex on Mac/Pi
  (Python 3.9+, flat `/home/pi/scripts/` on Pi); blocks protected-path recursive rm,
  forced pushes, and Mac-only destructive find/disk commands, with chain-local cd tracking.
  Fail-open; Jacob runs a deliberately blocked command himself with `! <command>` in Claude.
- `shared/claude-mods/clean-output/` is the Claude Code mod behind `/clean`
  (usage in its README). The Mac loads it from the checkout; the Pi, which
  has no checkout, loads the copy `zsh shared/claude-mods/deploy_pi.zsh
  clean-output` installs over `vanpi.lan` in
  `~/.local/share/claude-mods/clean-output/` (both through `settings.json`
  `env.CLAUDE_CODE_PLUGIN_DIRS`), so re-run it after any change to the mod;
  the mod's own `deploy_pi.zsh` calls it. Its engine-free modules (`hooks/*.ts` except
  `register.tsx`) also make `codexclean`, the same view of a Codex session:
  `codex/build.mjs` strips their types (Node >= 22.13) into plain modules that
  run on the Pi's Node 20, and `zsh codex/deploy.zsh` installs them on the Pi,
  then the Mac, in `~/.local/share/codexclean/` with a launcher at
  `~/.local/bin/codexclean`. The installed copies don't follow the checkout:
  re-run the deploy after editing any of those modules.
- `shared/claude-mods/term-highlight/` is a Claude Code mod that bolds the first
  mention of each term a reply's **Terms** block defines, by rewriting the
  `AssistantMessage` markdown before the engine draws it (the stored reply is
  unchanged). Mac only, loaded from the checkout through `settings.json`
  `env.CLAUDE_CODE_PLUGIN_DIRS`; the Pi has no copy.
- `shared/claude-mods/effort-shortcut/` is a Claude Code mod that adds
  `/e l|m|h|x|M`, `claudepm`'s effort letters, forwarding to the built-in
  `/effort` (which also saves the level as that model's default). The mod API
  has no effort setter, and `$.command.run` refuses calls from inside a hook,
  so it runs on the next tick. Mac only, loaded like term-highlight.
- `shared/claude-mods/agent-label/` is a Claude Code mod that puts the model
  and effort an agent runs at in front of its Agent call's description
  (`[sonnet 5.5 · high] Read the logs`), which the agent list under the prompt
  shows. The list keeps the description from spawn time, so the label is
  worked out at the call: the call's `model`/`effort`, then the agent file's
  frontmatter, then the parent's latest request (a 2.1.314 probe showed
  built-ins such as Explore inherit both). The agent's first request is then
  checked against the label, and a toast says when they differ. Aliases are
  shown by the id they last resolved to (kept in `$.store`, per machine). The
  Mac loads it from the checkout; the Pi loads the copy `zsh
  shared/claude-mods/deploy_pi.zsh agent-label` installs, as for clean-output.
- Pi video audio is hardwired: HDMI-A-1 → splitter box (HDMI to the monitor,
  audio to optical) → Sonos soundbar's optical input. `sns.sh rear_movie`
  switches the soundbar to optical and groups vonRear with it; VLC and Van TV's
  mpv play to ALSA HDMI 0, and PipeWire's null sink is expected, not a fault.
  `shared/python/sonos_tasks.py` owns `ensure_rear_movie` (read/repair, preserving
  rear volume); `pi/apps/video_library/players/sonos_check.py` owns the delayed,
  bounded, coalesced check after VLC starts/resumes. `pi/scripts/rear_movie_check.sh`
  serves direct `.bashrc` VLC starts; `vlcmd`/`pp` use the manager's API instead.
- `pi/scripts/ubnt_wifi.py` reports an uncertain Starlink antenna operation as
  `confirmation_pending` rather than a failure; the dashboard reconciles it
  against fresh radio state without replaying it.
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
  first timestamp (minute granularity). The dashboard's storage reader (van-dashboard
  repo) resolves snapshot references; read-only event readers detect v4/v5 columns explicitly
  and never turn lock or I/O errors into unweighted counts.
- Network storage deployment test filesystem fixtures live in
  `pi/tests/network/_network_storage_support.py`.
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
  worker and Mac heartbeat all honor it. Samba mount reconciliation clears only
  empty shutdown gates; nonempty capture gates are released by their owner.
  `pi/tmpfiles.d/vanpi-samba-drain.conf` recreates the root-owned capture gate
  directory at boot; the Mac worker also applies it in ExecStartPre. Setup must
  preserve existing gate files. Both scoped installers ship and apply it.
  Indefinite holds use `paused_until: "indefinite"`. Switching clouds locks both
  control records, pauses the peer indefinitely, then queues the selected job;
  never delete or force-release the common backup lock or stop local backups.
- Cloud upload speeds: `pi/scripts/backup/upload_speed_log.py` owns each job's
  `upload-speed.bin` in its state directory (one `<II` epoch/bytes-per-second
  sample per uploading minute, from rclone byte deltas; 30-day retention, pruned
  on every `--run` and hourly while appending). `icloud_backup.record_progress`
  feeds it for both jobs; a failed write never stops an upload. The status
  helpers' fixed `--upload-speeds` flag exports it for the dashboard's
  `/api/backups/cloud/<kind>/upload-speeds` graphs. Both cloud installers ship the module.
- Disk stats: the system monitor's `disk_history.py` keeps each disk label's
  per-minute read/write/busy record for 30 days beside its database (read with
  `system_event_monitor.py disk-io --latest` or `--label`), and `smart_watch.sh`
  logs every SMART reading to `/var/lib/vanpi-smart-watch/history/<label>`.
  `disk_history.py` deliberately repeats `upload_speed_log.py`'s append, torn-tail
  and hourly-trim code: the flat backup workers and the `system_monitor` package
  share no import root on the Pi, so one module would need a `sys.path` edit.
  The dashboard joins both for its `/api/disks/stats` and `/api/disks/history` graphs.
- Backup priority: `pi/scripts/backup/backup_priority.py` owns the cloud job
  registry and the protected until-verified override; `backup_priority_control.py`
  is the fixed CLI. Priority only decides which cloud job may take the single
  iCloud upload slot; local Borg/EXFAT and disk jobs are always admitted. Mount,
  ignition, uplink and manual-pause checks remain. Verification completion
  is latched so a later generation cannot reactivate an old override.
  `mac_capture_control.py` issues expiring, one-use stop permission bound to a live
  capture nonce. The root Mac coordinator verifies the active destination before
  `tmutil stopbackup`, then a later poll must prove idle and non-force detach.
  Both targeted cloud installers ship these modules
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
  `/run/lock/vanpi_backup.lock` shared on fd 9. `restore_from_borg.sh`,
  `new_hotspare.sh` and offsite `prepare-disk` take it exclusively through
  `acquire_job_lock`, which also takes `job-offsite`, so they exclude and are
  excluded by every job, an offsite run included. The deploy fence
  (`local_backup_begin` in `pi/backup_local_deploy.sh`) opens the existing inode
  and takes it exclusively, non-blocking, on fd 9 itself, never through
  `acquire_job_lock`: it refuses while any migrated job runs and no offsite run
  can start while it is held, but without `job-offsite` it publishes beside a
  running offsite run (see `offsite_backup.sh` below). Never create lock
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
  restoration or unit reload leaves timers stopped. The transaction is journaled
  in `/var/lib/vanpi-backup-deploy/` (`/tmp` is emptied at boot):
  `backup_deploy_state.sh` owns the marker vocabulary and admission guard;
  shell admissions recheck under their acquired legacy hold, and Python cloud
  admission rechecks after descriptor validation. Direct `clone_to_sd.sh` joins
  admission or verifies and preserves an inherited shared/exclusive legacy hold.
  `backup_deploy_trigger.sh` dispatches the transaction's own journaled recoverer.
  `backup_deploy_recovery.sh` restores preparing/publishing transactions but only
  finalizes a committed transaction (cleanup and recorded timers, never rollback).
  Contents and affected directories are flushed before commit/clear; pending stays
  until timer restoration succeeds. EXIT rereads and validates the privileged
  transaction marker, so deferred TERM after the commit rename cannot choose
  rollback from stale shell state; ambiguity leaves files and timers untouched.
  The independent bootstrap dispatcher, the
  hourly window and subsequent installers retry recovery. The installer records
  its PID so drains TERM first; the emergency grace remains unchanged.
  `pi/backup_deploy_bootstrap.py` stages and explicitly applies/verifies/releases
  one-time maintenance. `backup_bootstrap_plan.py` owns minimal edits and hashes
  for the actual reviewed predecessor; `backup_bootstrap_host.py` owns durable
  cron/service launch holds and the independent dispatcher; `backup_bootstrap_files.py`
  owns no-follow reads and durable files for that separately installed runtime
  (it intentionally has no shared/python import root). Staging supports Python
  3.9+; host execution requires Linux/Python 3.11+. All live/staged predecessors
  must retain guards. Broad sync deploys only non-backup destinations, excluded
  from the canonical union of both plan profiles; its receiver independently
  refuses backup-owned payload/cleanup overlap before writes and preserves existing
  parent-directory metadata. `pi/backup_sync_client.py` (Python 3.9+) uploads the
  inspection closure and holds the validated legacy inode shared through all sync
  phases; `backup_deploy_sync.py` (scope CLI 3.9+, host runtime Linux/3.11+) owns
  canonical exclusions, scope validation and privileged readiness using the existing
  bootstrap inspector/fence. The receiver retains its own shared hold, with no sudo
  descriptor-preservation requirement. Only inspection code uploads before readiness;
  maintenance/pending/incomplete/unsafe state refuses unrelated work. Healthy released
  and genuinely unbootstrapped hosts remain supported (an existing legacy lock is
  required). `connectivity_status.py` still deploys through this non-backup sync.
  Older publishers and unguarded rollback remain unsupported; bootstrap cannot
  revoke already-running old shells.
  Read-only preflight precedes holds and repeats under the fence. `abandon`
  archives only provably unpublished attempts after restoring/flushing crontabs;
  published attempts retain maintenance until same-bundle rerun/release. Cron
  holds/restores flush `/var/spool/cron/crontabs` before admission reopens.
  Exact admission snippets constrain upgrades; see the documented maintenance
  upgrade limit rather than removing readiness records. Time Machine `--check`
  checks installed bootstrap readiness and staged guards without recovery.
  Commands and operator boundary: `pi/docs/backup/DEPLOY_BOOTSTRAP.md`.
  `VANPI_BACKUP_PREVIOUS_REVISION` names
  the previously deployed commit when installing a newly committed change.
  `VANPI_BACKUP_PREVIOUS_CONF_SHA256` accepts an explicitly reviewed config hash
  when unrelated deployed settings differ from that commit. Previous-tree absence
  permits only a missing or exact staged live file.
  `pi/tests/backup/test_local_backup_deploy.py` exercises those shell contracts
  against temporary source/live directories and a fixture Git repository.
  `pi/time_machine_deploy_checks.py` is the Time Machine deployer's Python 3.9+
  target-test/source manifest: `--files` combines its check dependencies with
  `local_backup_sources tm`; `--run` runs the four isolated Linux target commands
  at most three at a time, without caching. `deploy_time_machine_icloud.sh --check`
  stages and checks only; it exits before the deployment fence or activation.
  `pi/tests/backup/test_time_machine_deploy_checks.py` exercises those public CLIs
  through private stages and a fixture SSH transport.
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
  `pi/docs/price_check/PRICE_CHECK.md`.
  Recovery retries silently at six-minute intervals, stops after 50 consecutive
  failed attempts and sends one ntfy alert. A successful search resets the
  durable count; preserve claim fencing and idempotent failure acknowledgements.
  `search_watch/store.py`'s `SearchStore.update_watch` preserves history and
  dismissals while clearing old-query current matches; `expected_url` guards
  in-flight check writes. Ship the flat price-check CLI/store/service cohort
  before a dashboard release that calls it.
- Deal Watch Haveno XMR offers: `pi/scripts/price_check/haveno_watch/` (`source.py`
  order book + reference price, `store.py` tables in the shared price-check DB,
  `service.py` matching/ntfy, `cli.py` `haveno-*` subcommands and `.haveno.lock`).
  `activity.py` runs market snapshots and classification; `activity_store.py` owns
  the inferred-offer activity tables and summaries.
  Order book is haveno.markets level-2 `asks` (RetoSwap only); reference is the Haveno
  pricenode with Kraken fallback, never the haveno.markets ticker. Each offer ID
  notifies once per watch. Its own 10-minute `haveno-check` cron line sits beside the
  price-check line.

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
  Settings conflicts cross the privileged CLI as `EX_TEMPFAIL` (the dashboard
  maps them to HTTP 409); never select a status from human error wording.
- `pi/scripts/backup/cloud_backup_requests.py` owns durable `backup-now.json`
  requests. Back up now bypasses only the weekly cadence, coalesces repeated clicks,
  resumes an unfinished generation and is satisfied by one new verified generation.
  It never rewrites success history, relaxes admission or discards checkpoints.

## Vanpi connections

Bare `vanpi` is the Pi's name in links, command examples and interactive
commands: `ssh vanpi` probes the LAN and falls back to Tailscale, and
`http://vanpi:<port>` resolves through the Mac's `/etc/hosts` line on or off
the van LAN (on the LAN, Tailscale goes direct). Never give `vanpi.lan`,
`vanpi.local` or a Tailscale IP in a link or an example: the first two
resolve only on the van LAN, and the IP lives in `~/.ssh/config` and
`/etc/hosts` alone. `vanpi.lan` belongs in code that must never cross the
metered uplink and has nobody to ask (scheduled jobs, syncs, bulk copies), so
it fails off the LAN instead of rerouting, and in URLs handed to LAN devices
without the Mac's hosts line (Sonos chime URIs, scripts running on the Pi).
Keep device identities, service names and filesystem paths unchanged.

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

Video-library, audiobooks, BME280 and the metered controller use allowlisted
immutable package releases owned by `pi/deploy_python.py`; the van dashboard
imports from them too (see [Van dashboard](#van-dashboard-separate-repo)).
First cutover of each service requires `--activate --service <unit>`; routine
`--update` refuses unactivated or flat-restored services. Backups under
`pre-package-units/<unit>.backup/` are write-once. Per-service state and pending
restart intent survive partial installs and failed reloads/restarts; every
completed unit also rewrites `activated.json`, broad sync's host gate. The
dashboard's scripts-era `pre-package-units/van-dashboard.service*` backups and
`service-state/van-dashboard.service.json` stay on the Pi as evidence; nothing
here reads them. See `pi/docs/deployment.md` for exact supervised cutover and saved-unit rollback.
The entire `/home/pi/scripts/python-automation/` directory is frozen: never write
or delete it. `--legacy-flatten` and the old video deployer (including v1 rollback)
now refuse; do not bypass them with an older deployer.

Routine `pi/sync_scripts.sh` stays pinned to `/Users/jacobr/dev/scripts` because
it publishes ignored private inputs. Its preflight requires host activation and
every package unit's service-state marker. It installs non-Python dependencies first,
package updates next, compute last. Generic unit staging excludes all package
units using the installer's `SERVICES` table via local `--list-units`. Use a
chosen clone's checkout-relative package installer for scoped updates without
private inputs; provenance records that clone. `--service` selects unit actions,
not release contents: the shared current link and utility code still advance.

`deploy_python.py` packages its own checkout's working tree (release
provenance records `dirty`). When another session has uncommitted changes here,
deploy from a clean `git worktree add --detach <dir> <commit>` and run that
copy's deployers, or its half-done work ships.

GC under `.install.lock` protects current, previous, just-installed, newest three,
and the verified running release of every package unit and of each
`EXTERNAL_CONSUMERS` unit (another repo's service that imports this release:
`van-dashboard.service`). Each entrypoint records pinned
`pi.__path__`, PID and INVOCATION_ID in `/run/<service-name>/package-release`.
Missing/stale active records or ambiguous service state retain everything,
including during partial flat cutover/rollback. Record-write failure only warns.
Factory smokes/custom long-lived consumers must hold a shared installer lock for
their full lifetime and stop before deployment. Never infer a running release
solely from current, previous or the last installer restart. Ad-hoc tools that
import a release (PYTHONPATH into `releases/` or `current`) must set
`PYTHONDONTWRITEBYTECODE=1`: GC skips everything when any release tree differs
from its manifest, so one stray `__pycache__` stops all pruning.

Never bundle `van_compute` (a separate repository with its own coupled
installer). The storage installer owns recorder, storage and logging files and
no dashboard file: historical storage manifests containing any dashboard target
(flat backend, unit, storage drop-in or React release) fail closed, including
rollback. The original recorder installer is retired; `deploy_network_flight_recorder.py`
refuses every action and keeps only the helpers the storage installer embeds.
Ship compatible recorder changes first with the storage installer's
recorder-only workflow, then the dashboard release that needs them; see the
network installer boundary in the runbook.

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
(public repo `https://github.com/JakeIwen/van-compute`); its `AGENTS.md` holds the
compute contracts. Nothing here imports it: the van dashboard reads
`van_compute.metrics` from `/home/pi/van_compute/current` and runs its own
compute-provider preflight. `pi/sync_scripts.sh` still runs the compute installer
from `VAN_COMPUTE_CHECKOUT` (default `/Users/jacobr/dev/van-compute`) as its last
phase, and its preflight refuses before any upload when that installer is missing.

## Repository test suite

Run `.local/test-venv/bin/python run_tests.py` from the repository root before committing or reporting
completion. It discovers tracked and unignored new tests, excludes deleted files,
and runs isolated Python unittest modules per file, Node tests, shell tests and
Claude mod tests. Missing interpreters/dependencies
are errors; executable availability is checked before suite commands start.
Tests needing specific OS features or hardware must state their skip reason.
Shell tests run under macOS `/bin/bash` 3.2, where `set -e` ignores a failing `[[ ]]` or `(( ))`, so assertions must fail explicitly (`[[ ... ]] || fail ...`).
macOS checks each newly written executable on its first run (about 70 ms, more under suite load). Fixtures that start many command doubles hard-link one copy per process (`command_double` in `pi/tests/backup/offsite_fixtures.py`) or run scripts through their interpreter, and never start a freshly written executable inside a short timeout.
Set `BTT_LIVE_TESTS=1` to also enable BTT's opt-in native bridge tests.
Use an interpreter with Flask and Werkzeug installed (this checkout has an isolated
`.local/test-venv/bin/python`).
`run_tests.py` owns the standalone-script import roots in `PYTHON_IMPORT_ROOTS`;
extend that registry instead of adding per-test `sys.path` patches.

- Parallel: commands run `-j N` at a time (default CPU count; `-j 1` is
  one-at-a-time), slowest first by the durations earlier runs recorded;
  commands never timed start first. Tests must therefore not share fixed
  temp paths, ports or global files. Each command's output is captured and
  shown together with `PASS <name> (<secs>s)` or `FAIL <name> (exit N)`.
- Fail fast: after the first observed failure no new command starts; running
  ones finish (their teardown matters), all failures print, and the final
  `FAILED:` line says how many commands were not run (exit 1). Not-run
  commands are not a pass. A command over 900 s fails and its process group
  is killed; `--timeout SECONDS` overrides that deadline. Ctrl-C/SIGTERM
  kills active groups, releases the lock and exits 130/143. Test commands don't
  inherit the lock, so a process a test leaks can't block later runs.
- Pass cache: the checkout-specific sha256 covers fresh bytes and file
  metadata for tracked/unignored inputs (never trusting Git's stat cache),
  symlink targets, submodule contents, `.local/test-venv`, the active venv,
  and `node_modules` beside package manifests. It also covers the
  interpreter/platform, resolved tool binaries, `PATH`, `PYTHONPATH`,
  `BTT_LIVE_TESTS`, `OFFSITE_RESTIC` and `OFFSITE_REST_SERVER` (including their
  resolved executable bytes). `.claude/worktrees/` belongs to other runners
  and is excluded from both discovery and fingerprints.
  A pass within 6 hours prints `PASS: all N suite commands completed
  (cached: ...)` and exits 0; `--force` reruns and revokes the earlier pass
  before starting. Live BTT tests (`BTT_LIVE_TESTS=1`) always run. Use
  `--force` after changes to other external services or undeclared inputs.
  A pass is recorded only when inputs are unchanged after the run; unreadable
  inputs disable caching rather than silently disappearing from its key.
- Coalescing: runs take an exclusive lock before fingerprinting and discovery,
  so a second session waits ("Waiting for another run_tests.py run...") and
  then usually hits the cache for the same checkout and inputs. Runners from
  before this change don't take the lock.
- State lives in `<git common dir>/run_tests/` (`timings.json`,
  `passes.json`, `lock`): untracked and shared by all worktrees. Corrupt
  JSON is discarded; writes replace files atomically. The runner's CLI
  regression tests are `tests/test_run_tests.py`.
- Selection: `--path PREFIX` (repeatable) runs only tests under those
  repository paths and `--exclude-path PREFIX` drops some; both match whole
  path components, a prefix that matches no repository path is an error, and
  output lines name the selection. A selected pass has its own cache key, so it
  never certifies the whole suite (whose key is unchanged).

### Linux test lane

Mac skips are not Linux passes: Linux-only cases (fdinfo/flock lock evidence,
Bash 4+ backup shell, real Borg/rclone, user namespaces, GNU tools) run in a
Debian bookworm VM on the Mac (`macbook/linux_test_lane/`), never on the Pi.
It is opt-in: plain `run_tests.py` never starts it and no deployer calls it.
Setup is `brew install lima`; from the checkout root:

- `macbook/linux_test_lane/linux-test-lane up` creates, starts and provisions
  the VM (8 CPUs, 8 GiB, 40 GiB) when needed; reprovisions when
  `provision.sh`, `pins.env`, `requirements.txt` or either upstream binary
  installer changes.
- `... test` runs `up`, syncs this working tree (tracked plus unignored files,
  deletions included) and runs the VM's `run_tests.py --path pi --path shared
  --path synology --path tests --path macbook/tests/test_linux_test_lane.py
  --exclude-path shared/claude-mods`, ending
  with the runner's PASS/FAILED line and exit status; the VM keeps its own pass
  cache. Every invocation checks required tools and their pinned versions,
  including both offsite binaries, even with a current provisioning stamp;
  missing/drifted tools fail before cache lookup. To restore the checked-in
  pins, run `macbook/linux_test_lane/linux-test-lane up --repair`: it retains
  the destination guard, reinstalls distro packages, reruns the pinned
  installers and recreates the test venv, then verifies everything before
  writing the stamp. Plain `up` with a current stamp only verifies; it does
  not repair. Change `pins.env` only for a deliberate runtime upgrade, never
  to hide a missing tool. Source and Git copies use rsync checksums,
  not size/mtime guesses. Before provisioning (including a stale/missing stamp)
  and each rsync the guest rejects redirected, symlinked or non-owned
  checkout/`.git` ancestors outside its fixed lane root.
  `--path`/`--exclude-path` replace that selection; `--force`, `-j`
  and `--timeout` pass through. shared/claude-mods is left out because its
  suites run through the Mac's `claude plugin test` and its codexclean build
  needs Node >= 22.13; the Pi only receives built copies.
- `status`, `shell` (a shell in the VM checkout), `stop` (it otherwise stays
  running) and `destroy` (deletes the VM and disk, for a clean start).

Lane tests always set `OFFSITE_RESTIC` and `OFFSITE_REST_SERVER` to
`/home/pi/linux-test-lane/venv/bin/offsite-restic` and `offsite-rest-server`.
The first is a symlink to the VM's verified restic 0.19.1; the second is the
archive-verified rest-server 0.14.0, installed only in the test venv. Receiver
and REST acceptance tests therefore execute rather than skip. Host overrides
are not forwarded: these values are fixed by the tracked lane code, and the
runner fingerprints both environment values and resolved binaries (also through
its active venv), following symlinks.
Provisioning also installs rsyslog for the separate privileged spool check.

Lane commands serialize on `~/.local/state/linux-test-lane/lock`, since every
worktree shares the VM's one checkout. The VM user is `pi` (uid 1000) with
passwordless sudo, like the Pi; tests still run unprivileged, so root-only
cases (`test_time_machine_runtime`, the rsyslog spool test) skip there too.

## Deployment commits

- After a successful deployment from this repository, commit and push the
  deployed repository changes once validation passes. Keep unrelated worktree
  changes unstaged and out of that commit.

## Van dashboard (separate repo)

The van dashboard (`van-dashboard.service` on port 8788: Flask backend and React
frontend) lives in `~/dev/van-dashboard`, local only for now; its `AGENTS.md`
has the layout, tests and deploy. Its `scripts/deploy.py` publishes backend
releases to `/home/pi/van_dashboard/releases/` and frontend releases to
`/home/pi/van_dashboard/frontend/releases/`, installs the unit and its
`van-dashboard.service.d/network-storage.conf` drop-in, restarts, health-checks
and rolls back. Nothing here deploys, restarts or rolls it back. Its unit runs
`/usr/bin/python3 -P -m van_dashboard` with
`PYTHONPATH=/home/pi/van_dashboard/current:/home/pi/scripts/python-packages/current:/home/pi/van_compute/current`,
so it runs on code this repo ships. Keep this contract (that repo's `AGENTS.md`
mirrors it):

- Package modules: `pi.apps.metered` (controller, policy_requests),
  `shared.metered` (records, store), `shared.python.tool_settings`,
  `pi.package_runtime` and `pi.apps.media_organizer.{config,records}` stay in
  the package release, as do the `python3 -P -m pi.apps.media_organizer` and
  `-m pi.apps.iptorrents_watch` CLIs it runs.
  `pi/tests/test_python_deployment_consumers.py` stages the real release and
  imports each one.
- Helper scripts keep their `/home/pi/scripts/...` paths: backup, cloud, disk,
  network, power, Tuya, ntfy, price check and system monitor tools named in its
  `van_dashboard_common.py` and controllers; its unit's `ExecStartPre` checks
  `safe_reboot.sh` and `safe_power_down.sh`. The JSON they print for it
  (`offsite_backup.sh dashboard`, the cloud status helpers' `--upload-speeds`,
  `system_event_monitor.py disk-io`, `network_flight_recorder.py report`, the
  media organizer and IPTorrents records) are wire contracts.
- Its `__main__` writes `/run/van-dashboard/package-release` naming the scripts
  release it imported. `deploy_python.py` lists the unit in `EXTERNAL_CONSUMERS`,
  so release GC keeps that release while the dashboard runs.
- Until the Pi is cut over, a release would strand the scripts-era unit, which
  runs `-m pi.apps.van_dashboard` through `current`. The receiver refuses
  `--activate`/`--update` unless systemd's effective state for each
  `EXTERNAL_CONSUMERS` unit matches `CONSUMER_LAUNCH_FORMS`: `/usr/bin/test`
  with any args, or `/usr/bin/python3 -P -m van_dashboard[.<submodule>]` with
  separate tokens and optional args. Explicit `systemctl show -p` properties
  exclude ExecMain metadata and retain empty scalars. Every complete effective
  command and non-empty on-disk Exec directive is checked (prefixes stripped,
  reset lines ignored). Unknown launchers, malformed state, a pending reload or
  an unreadable fragment/drop-in refuse; a genuinely absent unit passes.
  `pi/sync_scripts.sh` runs the same check (`--check-consumers`) before any upload.
  Deploy the van-dashboard repo first (its `docs/cutover.md`).
- A scripts deploy that changes those modules does not restart the dashboard: it
  keeps the release it pinned at start. Restart it afterwards
  (`ssh vanpi sudo systemctl restart van-dashboard`) or redeploy it.
- Its HTTP API on `:8788` is a contract here too: `macbook/scripts/van_dashboard_http.py`
  (lighting, cab dimmer), the video library's dashboard link and the storage
  installer's readiness check (`/api/network-history`).
- `pi/crontab_pi`'s managed `hosted-project-scan` block runs
  `-m van_dashboard.hosted_project_scan` from `/home/pi/van_dashboard/current`
  (that repo's `docs/HOSTED_PROJECTS.md`). The repo's crontab files lag the live
  ones in unrelated lines: run `pi/pull_crontabs.sh` and review the diff before
  any `pi/push_crontabs.sh`.

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
- `shared/sh/codex_update.py`: keyed Codex/Claude weekly update adapters on both hosts, with independent cadence/results in `~/.local/state/codex-update/state.json` (legacy flat Codex state is preserved). The filename and six-hour cron lines in `macbook/crontab` and `pi/crontab_pi` stay stable; the Pi copy remains `/home/pi/scripts/codex_update.py`. Downloads require bounded host-local `allows pi.<tool>_update` via `/home/pi/scripts/vanpi-metered`, or `allows mac.<tool>_update` via `/Users/jacobr/dev/scripts/macbook/metered/mac-metered`; missing/failing/timed-out permission defers as `metered or unknown`, and `--force` skips only cadence. Every scheduled Claude check first atomically merges only `env.DISABLE_AUTOUPDATER="1"` into the existing regular `~/.claude/settings.json`, even when denied/not due; invalid JSON is refused. Both native CLIs run from `~/.local/bin` with it on PATH. Marketplace auto-update is unchanged: on-demand refresh exists but the inspected CLI has no auto-update toggle. The standalone `-I` flat copy keeps its atomic JSON writer local because checkout helpers are outside its import/deploy contract. `shared/automations/codex-update.json` shows both tools on both hosts, with bounded status SSH over `vanpi.lan`. Both hosts' `~/.codex/config.toml` set `check_for_update_on_startup = false`, so the TUI never prompts.
- `pi/scripts/backup/abort_backup.sh`: coordinated termination before unmounting.
- `pi/scripts/backup/clone_to_sd.sh`: bootable spare-card cloning.
- `pi/scripts/backup/offsite_backup.sh`: encrypted restic offsite drives, seeded
  locally then served by a friend's Synology over Tailscale. `prepare-disk` holds the
  exclusive legacy lock like `restore_from_borg.sh`; backup, check, receipts,
  restore-test and the resume worker hold `job-offsite` (fd 10, PID record), hold the legacy lock shared only while
  `offsite_snapshot.py` copies their code, then exec that frozen copy, so the cohort
  installers, nightly Borg and EXFAT run beside a days-long seed. `acquire_job_lock`
  also takes `job-offsite`; the deploy fence does not, and its registry guard
  (`local_backup_registry_guard`) refuses to unregister a held lock. `offsite_snapshot.py`
  creates, verifies and deletes that per-run copy under `/var/lib/vanpi-offsite/code/`
  (descriptor-relative deletion that never follows links or crosses mounts);
  `offsite_locks.py` proves the launcher's hold and owns the run's cloud-slot, disk
  and `data-borg-repo` descriptors (non-blocking but for the bounded Borg wait,
  released by close). `offsite_config.py` owns sources, excludes, lock policy
  constants, the frozen closure / live data / live boundary sets
  (`test_offsite_snapshot_closure.py` rescans them) and canonical
  marker/transport/receipt records (NAS Python 3.9+, Pi runtime 3.11+). Persisted
  state is additive-only (`test_offsite_state_compat.py` checks v2 both ways).
  `offsite_repository.py` verifies local mounts/IDs; `offsite_prepare.py` guards
  erasure; `offsite_samba.py` owns the Time Machine capture gate; `offsite_run.py`
  writes status/processed-versus-packed byte metrics; `offsite_mounts.sh` reuses
  label helpers. `offsite_remote.py` owns known-ID REST auth and fresh
  `pi.offsite_backup` metered/Tailscale admission for uploads and readback.
  `offsite_receipts.py` commits/read-verifies encrypted completion receipts instead
  of deleting snapshot tags on append-only targets, and owns check/restore evidence.
  `offsite_dashboard.py` owns durable per-label/source `history.json` (`latest` and
  `last_ok`) and read-only `dashboard`/`rates <source>` exports. Logical per-source
  rates reuse `upload_speed_log.py` in private `rates/<source>.bin`; history/rate
  failures never abort backups, and malformed history is retained with a status error.
  `offsite_control.py` owns per-label durable remote holds and revision-bound source/repository
  plans (`offsite_backup.sh pause|resume --remote --label LABEL --revision REVISION`).
  Workers poll the hold through their existing cancellation path; saved PIDs are never signalled.
  Resume starts one transient guarded attempt, not a timer, schedule or local seed.
  `offsite_availability.py` reads bounded local Tailscale/route evidence for the dashboard;
  it never contacts the receiver and never claims an online peer proves a mounted target.
  Both modules ship in the targeted cohort and frozen closure; provider deploy precedes dashboard.
  `install_restic.sh` pins restic 0.19.1. No schedule or automatic retry is installed.
  Runbooks: `pi/docs/backup/OFFSITE.md` and `OFFSITE_NAS.md` beside it.
- `synology/offsite/`: native DSM receiver and explicit installer. `mounts.py`
  selects exact mounted, marked USB filesystems; `runtime.py` pins the repository
  directory descriptor, reaps the owned server before switching, and binds auth
  to the active repo ID so stale clients cannot write into a replacement drive.
  `control.py` owns persistent stop and singleton lifecycle; DSM boot/watchdog tasks
  call installed `receiver boot`. `binaries.py` pins rest-server 0.14.0; `install.py`
  copies the canonical offsite config and protected reader beside the receiver.
  Only a separate transport secret goes to the NAS, never the encryption password.
  The small NAS durable JSON writer is intentionally host-local: importing the Pi
  writer would cross the independently installed runtime boundary.
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

Both installers journal each transaction in the root-only
`/var/lib/vanpi-backup-deploy/<id>/` and print its `previous` directory on
commit; the newest five are kept. Older `<stage>/previous` directories (in
`/tmp`, gone after a reboot) and flat `previous/` directories retain their
saved recovery procedures. `previous/tree/`
mirrors absolute destinations (for example `tree/home/pi/scripts/backup/` and
`tree/etc/systemd/system/`). `previous/manifest` has one pipe-delimited record
per plan entry: source, destination, old SHA-256 or `absent`, old owner:group,
old mode, staged SHA-256, staged owner:group, staged mode, replace/keep action,
and temporary path (suffixed with the installer's PID). All originals and
replacements are prepared, and the manifest made durable, before the first
publication. `active-timers` records which timers were active before the fence,
`recovery/` holds the recoverer with every library it sources, and the stage's
`transaction/published` lists attempted destinations.

Ordinary failures automatically restore attempted replacements in reverse
order while fd 9 still holds the exclusive legacy fence. Cleanup ignores repeated
INT/TERM/HUP until it ends, so a second stop cannot cut rollback short. It checks
both content and owner/mode, removes only verified newly introduced files,
and reloads restored systemd units before restarting any prior active timer.
An incomplete restore/reload prints `ROLLBACK INCOMPLETE`, exits nonzero and
leaves timers stopped. The rollback directory is retained. From before the first
live change through commit/restoration **and timer finalization**, pending names
its transaction and admission refuses. The independent bootstrap dispatcher at
`/usr/local/lib/vanpi-backup-deploy/recover` survives every cohort rollback and
runs hourly; the window and next installer also retry. Preparing/publishing
transactions restore the guarded predecessor and reload units; committed records
verify the new cohort and finish cleanup/timers without reverting it. Both paths
flush affected files/directories before clearing pending. A failed attempt retains
pending and warns daily; some timers may already have restarted, but their jobs
still refuse. To retry after repairing a reported conflict, run
`sudo /bin/bash /usr/local/lib/vanpi-backup-deploy/recover`.
Never delete `pending` by hand: jobs would then run a mixed cohort. Activation actions
(including Time Machine capture requests) occur only after the cohort commits;
their failure reports a committed cohort with incomplete activation.

Prefer a guard-aware targeted installer for a later intentional rollback. The
manual tree procedure below is not journaled and needs separately arranged
durable launch holds plus the operator maintenance boundary until verification;
it is not covered by automatic recovery. First pause both cloud jobs with the existing
controls, preserve independent holds and let local jobs finish. Inspect the
saved manifest, private tree and current hashes before writing. Use the staged
`pi/backup_local_deploy.sh` contract, not a newer installed helper, and source
the staged `pi/scripts/backup/backup_locks.sh`; once the stage is gone, source
the transaction's `recovery/` copies of `backup_deploy_transaction.sh` and
`backup_locks.sh` instead. Validate and open the existing
legacy inode with `backup_lock_open`, acquire its exclusive
flock and stop the four cloud timers. Set `LOCAL_BACKUP_TXN` to the saved
transaction directory and `LOCAL_BACKUP_TEMP_ID` to your shell's PID, initialize
`LOCAL_BACKUP_TEMPORARIES=()`, and load the reviewed manifest records into
`LOCAL_BACKUP_PUBLISHED`. `local_backup_rollback` restores those
records and reloads units; `local_backup_cleanup_temporaries` removes its
staging files. Do not restart timers or release the fence before restoration
is verified. Restore only the previously active timers, then resume the
selected cloud job through its control entry point. Never restore a predecessor
without the bootstrap admission guards or remove the independent dispatcher,
cron entry or readiness record. The journaled recoverer finalizes committed
records but never intentionally rolls back a committed cohort.

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
