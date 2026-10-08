# BetterTouchTool tooling

Use one maintenance entry point:

```sh
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py --help
```

The underlying current script paths remain available for compatibility. Runtime
providers and app-specific installers have not moved. No command invokes an
archived fix automatically.

## Maintained commands

All style/setup/repair commands **inspect by default**. Add `--apply` deliberately
to change BTT; each underlying updater retains its backup and verification checks.
Most API-backed commands must run from the user's Terminal because Codex's native
Apple Events access is sandboxed. `inspect` and `backup` need no Apple Events.

| Command after `btt.py` | Purpose |
| --- | --- |
| `inspect` | Read-only saved Media summary, including unassigned buttons; no action execution. |
| `backup` | Private, consistent SQLite configuration snapshot under ignored `tmp/`. |
| `guard …` | Known-good checkpoints, read-only audits, approved repair and scheduled ntfy warnings; see below. |
| `restart` | Clean restart through the existing maintenance helper; no button execution. |
| `restore-media` | Inspect recovery of a **deleted** Media tree from the latest appearance backup; `--apply` restores missing records individually. Optional `--source BACKUP.json`. |
| `style main` | Main-bar typography, partymode centering, original speaker icons. |
| `style transport` | Original transport artwork and `20s` / `5m` captions. |
| `style status` | Center play icon and status-text colors only. |
| `status` | Playback-status output and compact row spacing. |
| `notes` | Recent/Pinned Notes setup; `--labels-only --apply` limits edits to labels. |
| `escape` | Escape dismissal while the three Media dropdowns are visible. |
| `repair rps` | Restore the missing sync-button link without running a deployment; optional `--with-escape`. |
| `repair sizes` | Repair conflicting min/max dimensions without normalizing valid custom sizes. |
| `repair paths` | Repair reviewed moved-script paths in active floating-menu actions and named triggers; `--apply` changes command fields only. |
| `repair actions` | Recover empty Media buttons from prior exports; `--apply` backs up, adds individual actions, and **restarts BTT to verify persistence**. |
| `repair dependencies` | Preview missing named dependencies; `--apply` copies unambiguous simple dependencies without enabling old presets, then verifies after restart. |
| `repair persistence` | Targeted speaker/Notes recovery; **restarts BTT when applied**. |
| `restore-sizes BACKUP.json --apply` | Guarded restoration of a sizing repair's prior values only. |

For example, preview a style change, then apply it:

```sh
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py style main
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py style main --apply
```

`backup` saves the SQLite configuration, not the external preset images,
preferences, clipboard history, or whole application state. It is **not a complete
application restore point**. Existing full-folder rotating backup automation
(`btt_backup.zsh`) is retained, but is legacy: run it with BTT stopped and understand
that it replaces an old backup slot. No generic full-database restore is exposed
by the new CLI. Never overwrite live Core Data files while BTT is running.

`restore-media` is narrower: it requires the main Media tree to be entirely
absent, matches an exported tree to its companion SQLite snapshot, verifies the
original active Master preset and image files, and takes a fresh safety snapshot.
It restores each item/action with an explicit parent through BTT's scripting API.
The root stays disabled until the saved tree is verified. Existing menus are
never overwritten; it neither restarts BTT nor executes button actions. It also
replays the selected appearance backup's final styling patch. A backup may itself
contain previously unassigned buttons; restoration does not invent their actions.

```sh
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py restore-media --apply
```

If recovery reports a partial save failure, do not delete the partial menu or
import a full preset. Once BTT has saved its disabled root, inspect a guarded
resume with `restore-media --resume`. Add `--apply` to continue. Resume requires
a matching pre-creation backup and exact saved records; it skips those records
and creates only missing descendants. Changed/enabled roots or unexpected
children are rejected. Save checks poll for up to60seconds and stop immediately
when complete; they no longer abort after8seconds.

## Code organization

### BTT Guard: known-good recovery and warnings

Commands below are run from the repository root. `guard inspect` checks the
current graph even before there is a checkpoint. Checkpoints require the owning
user's Terminal because their runtime export uses BTT's Apple Events API.

```sh
./macbook/bettertouchtool/btt.py guard inspect
./macbook/bettertouchtool/btt.py guard checkpoint --accept-current
./macbook/bettertouchtool/btt.py guard audit
./macbook/bettertouchtool/btt.py guard repair
./macbook/bettertouchtool/btt.py guard repair --apply
```

Checkpoint promotion is explicit; replacing one also requires `--replace`.
The active pointer never advances during ordinary audits, BTT upgrades, failed
captures, or repairs. Prior generations are retained without automatic pruning.
Each generation contains a consistent SQLite backup, validated runtime exports,
flat recovery definitions, and self-contained copies of managed preset images.
Files are private and checksummed under ignored `.local/btt-guard/`. Runtime and
saved state must agree, and unresolved dependencies/empty active buttons block
promotion. Failed export validation preserves private diagnostics under
`rejected-captures/`, never a replacement known-good checkpoint.

Audits compare UUID relationships, active presets, app scopes, action payloads,
enabled flags, action sequence, scripts and operational menu settings. Pure label,
font, color and main-item reorder changes do not trigger warnings. Named triggers
and referenced floating dropdowns are included. Unreadable databases, unsupported
schemas or invalid checkpoints are **unavailable**, never silently healthy.

Repair is preview-only without `--apply`. It creates missing records individually
and reattaches genuinely detached records; detached actions also regain their
sequence position. Changed commands, a new owning parent, disabled dependencies,
missing presets or ambiguous scope stop the repair for review. It never edits
live SQLite, runs a button, activates an old preset or imports a replacement
menu tree. A safety backup precedes writes, followed by a clean BTT restart,
saved-state audit and runtime verification. Legacy unscoped named triggers can
be checkpointed/audited but are not automatically recreated if deleted: their
app binding needs explicit review. Integrity checks are not playback tests;
external scripts, services, network connectivity and arbitrary external icon
files remain outside this configuration monitor's guarantee.

```sh
./macbook/bettertouchtool/btt.py guard service prepare
./macbook/bettertouchtool/btt.py guard service install
./macbook/bettertouchtool/btt.py guard service verify
./macbook/bettertouchtool/btt.py guard service status
```

The optional boot-time `com.jacobr.btt-guard` LaunchDaemon runs as the owning user
once per minute, with absolute interpreter/workspace paths and private logs.
This is a finite scheduled check, not a continuously running web app: it uses
`StartInterval`, not KeepAlive or an HTTP server. `prepare` only writes/lints the
plist. `install` uses sudo only for the plist/launchctl operations. `verify`
requires a fresh completion heartbeat from the real registered job. Do not call
it installed merely because preparation or a foreground monitor pass succeeded.
`service stop/start/uninstall` controls only this matching registration; state
and checkpoints are retained. Uninstall before moving the checkout.

Warnings use `NTFY_WARNING_URL` sourced privately from `pi/secrets/.bash_variables`.
The URL is never placed in process arguments, logs, plists or checkpoints.
Two identical unhealthy checks at least 60 seconds apart produce an alert;
successful alerts are deduplicated, failed deliveries retry after five minutes,
and two healthy checks produce one recovery notice. Alerts contain generic
finding counts only, not commands, labels, note titles or UUIDs. `last-audit.json`
is the private detailed report; a failed delivery does not erase the warning.
Malformed monitor state stays preserved while a separate recovery-state file
maintains bounded warnings and unavailable heartbeats. `guard notify-test --send`
sends one clearly labelled test; `guard monitor --no-notify` runs a local pass.

`guard service status` distinguishes job supervision from `audit_status`; a
running monitor reporting unavailable is not proof that the menu is healthy.
As with any local monitor, a stopped/uninstalled job cannot notify about itself.

### Missing action assignments

`btt.py repair actions` checks all enabled standard items in the current Media
tree and finds the newest export that still has actions for each empty item.
Existing actions, names, layout, icons and item order are preserved. Applying
creates explicit child records (not a nested menu reimport), repairs reviewed
stale paths in restored commands, and checks named-trigger dependencies. A simple
dependency present only in one inactive preset can be copied into Master without
enabling or modifying that old preset. Ambiguous or unsupported recovery refuses
to proceed. No speaker, media, clipboard or sync action is executed by the repair.

Apply backs up the current database and runtime definitions before writing,
checks API associations, then quits/reopens BTT cleanly and verifies saved parent
links, actions, enabled flags and named-trigger resolution. It does not wait for
the asynchronous save timer and mistake a delayed save for permanent loss.
No force quit or automatic rollback is attempted if verification fails.

The supported scope is restoring **empty** existing buttons, not rolling back
modified action chains or deleted buttons. An offline database/export can be
inspected with `repair_media_actions.py --database ... --source ... --inspect`;
an alternate database cannot be applied. API plan validation and read-only
inspection tests do not contact speakers or send media commands.

### Stale script references

```sh
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py repair paths
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py repair paths --apply
```

The read-only preview checks the current saved configuration and verifies that
replacement files exist. The updater takes a private SQLite/action-export backup,
checks for concurrent changes, and patches individual command fields through
BTT's API. No parent menu is reimported; arguments, layout, titles and ordering
are preserved. Disabled entries, inactive presets and Touch Bar records are
excluded. Mappings are reviewed exact file moves, not broad directory rewrites.
The old missing Python3.9 interpreter is replaced only for the migrated,
standard-library-only wake-device helper. No action is executed during testing.

BTT can delay disk saves beyond60seconds. Runtime readback and saved-state
verification are reported separately; if saving is pending, don't restart yet.
Run the preview again later: zero stale actions confirms the path changes reached
the saved configuration. Do not rerun Media restoration to fix paths.

The Sonos launcher `macbook/scripts/sns.sh` sets its module path from its own
checkout location before using the Sonos virtualenv. BTT runs a noninteractive
shell and does not load Terminal's `.zshrc` PYTHONPATH. Python output is unbuffered
and stderr is merged into the result stream so failures appear in BTT's script
result. Changing this launcher takes effect on the next button use without a
BTT import or restart. Wrapper regression tests use a fake Sonos module and never
play audio or discover speakers.

### Sources

- `btt.py`: maintained CLI; fixed argument-list dispatch, no shell interpolation.
- `btt_common.py` / `btt_common.js`: shared read-only database checks, private
  verified snapshots/exports, and canonical comparisons. Importing them never
  sends BTT actions, changes configuration, or restarts an app.
- `lib/media_recovery.js`: pure recovery planning; no imports of retired repair
  executables are needed by the current persistence helper.
- `port_media_icons.*`, `tune_media_status.js`, `fix_menu_sizes.js`,
  `install_notes.js`, `install_menu_escape.*`, `repair_rps.*`, `stabilize_media.*`:
  implementations behind the CLI. Existing paths remain supported.
- `repair_script_paths.py/.js`: read-only discovery and guarded field-only path
  repair, with independent runtime and disk verification.
- `repair_media_actions.py/.js`: missing-assignment recovery; the Python module
  defines the `ActionRepairPlan` wire contract consumed by its JXA worker. Reuses
  common snapshots, action normalization/path mappings and clean restart helpers.
- `btt_guard/`: package-based guard; `model.py` owns wire/status contracts,
  `database.py`/`audit.py` read and compare saved state, `definitions.py` and
  `checkpoint.py` validate immutable recovery material, `repair.py`/`worker.js`
  implement approved restoration, and `monitor.py`/`notify.py`/`service.py` own
  warning state, private delivery and boot scheduling. `storage.py` shares private
  atomic file writes/locking. Legacy standalone helpers are called through their
  CLI instead of introducing import-path shims.
- `install_*_menu.*`, `build_performance_audio_menu.py`, `tools_menu_style.py`:
  app-specific installers for Audio, BPM, Rhythm, video conversion and metadata
  stripping. Kept at their established paths for their build/install workflows.
- `play_status.js`, `recent_notes_content_script.js`, `btt_widget_spotify.scpt`:
  content-script sources retained in place. Notes' current provider is
  `../scripts/notes_menu.py`; screenshots use `../scripts/capture_btt_menu.zsh`.
- `btt_touchbar_folder_to_floating_submenu.py` and
  `create_floating_dropdown_from_submenu.py`: reusable offline conversion tools;
  inspect generated definitions before importing them.
- [one_off_fixes/](one_off_fixes/README.md): dated, guarded historical repairs and
  failure reproductions—not routine maintenance. Known crash paths have ALL CAPS
  warnings in their filenames.
- `presets/`: small, reviewed reusable presets, not full personal configuration dumps.

## Data hygiene

Keep generated definitions, screenshots, extracted icons, diagnostic probes and
configuration snapshots in ignored `tmp/` or `macbook/build/`. Those can contain
private action payloads or credentials. Do not commit full BTT exports, databases,
secrets, or temporary diagnostic output. Root-level export/backup paths are also
ignored to reduce accidental inclusion; reviewed source fixtures remain separate.

## Documentation and tests

- [Media controls and appearance](../docs/BTT_MEDIA_CONTROLS.md)
- [Notes menus](../docs/NOTES_MENUS.md)
- [Sizing and restoration](../docs/BTT_MENU_SIZING.md)

Tests use fake BTT APIs or read-only/generated SQLite fixtures; they do not click
RPS, change speakers, import presets, or restart BTT. Native image/RTF tests require
macOS but do not contact BTT's scripting interface.
Behavior tests cover maintained tools, not archived one-off repairs. Archive
checks only protect the current boundary: historical tools stay inert by default
and maintained tools do not import them.

```sh
cd ~/dev/scripts
node --test macbook/tests/test_*.cjs
/usr/bin/python3 -B -m unittest discover -s macbook/tests -p 'test_btt_maintenance.py'
```
