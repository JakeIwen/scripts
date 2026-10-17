# BetterTouchTool tooling

Use one maintenance entry point:

```sh
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py --help
```

The underlying current script paths remain available for compatibility. Runtime
providers and app-specific installers have not moved.

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
| `repair text` | Preview selection-only text transforms; `--apply` uses BTT's local socket, backs up, disables the old copy/delay actions, and replaces the paste actions. |
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

### Media suite checkout

Performance Audio, Convert Video, Custom Conversion, BPM Over Time, Rhythm
Practice, Interval Analysis and Temp Card Printer live in `~/dev/mac-media-tools`.
Their app, scientific Python runtime,
job data and reports are under that checkout's `build/`. The menu installers and
builders stay here; generated presets and configuration backups still go to
`~/dev/scripts/macbook/build/`.

`btt_common.MEDIA_TOOLS` is the shared location resolver: `MAC_MEDIA_TOOLS` can
override its default. Python installers pass that resolved path to their JXA
workers; direct worker invocation must supply the matching suite-path argument.
`btt_common.py --media-tools` prints the same path without contacting BTT, for
shell consumers such as the video-thumbnail FFmpeg fallback.

For the reviewed media checkout migration only, run the path updater directly
from this repository root:

```sh
./macbook/bettertouchtool/repair_script_paths.py --media-tools --inspect
./macbook/bettertouchtool/repair_script_paths.py --media-tools --apply
```

This mode prints each action's old/new command, verifies replacement paths, and
uses the existing private backup/concurrency/API/disk checks. Unlike ordinary
`repair paths`, it includes disabled legacy suite actions so re-enabling them
cannot resurrect stale paths. It changes command fields only, never activation,
layout or ordering; BTT backups, other apps and unrelated scripts are excluded.
It does not rebuild the app, move data or promote a Guard checkpoint. Move data
only after checking processes and job state; preserve custom presets and report
identities. Recreate venvs rather than moving them.

### Sources

- `btt.py`: maintained CLI; fixed argument-list dispatch, no shell interpolation.
- `repair_text_transforms.py/.js`: guarded installer for the nine existing text
  transform buttons. `btt_socket.js` calls BTT's bundled CLI through its local
  socket, avoiding Apple Events for configuration changes. Enable Command Line /
  Socket Server once in BTT's scripting settings and restart BTT before applying.
  The socket is restricted to the owning user; no network webserver is needed.
- `text_selection.js`: runtime Accessibility adapter, executed inside BTT via
  `runJXA`. It reads the focused control's selected text/range, checks that the
  selection is writable and unchanged. Native editors use `AXSelectedText`; Signal and Firefox
  delegates replacement to `text_menu_paste.js`. Unsupported controls or missing
  selections stop with a notification. After replacement it selects the result using
  native text markers in Firefox and UTF-16 ranges in other apps. If the app refuses reselection,
  the notification explicitly says the text was already transformed.
  Each button has a readable entry script supplying its transform name to the shared
  runtime, without a separate named trigger or shared mutable parameter variable.
  `BTTActionJSRunInSeparateContext` isolates each entry's execution so BTT cannot
  reuse another button's same-named function from its shared JavaScript context.
  Electron editors such as Signal expose their text controls only after accessibility
  support is enabled. The handler requests the focused app's documented
  `AXManualAccessibility` flag when available and disabled, then allows up to half a
  second for its editor tree to appear. It aborts if the active app changes during
  that wait. Native editors without the flag keep the immediate path. This uses
  [Electron's accessibility interface](https://www.electronjs.org/docs/latest/tutorial/accessibility),
  without copying text, sending shortcuts, or touching message-send controls.
  Runtime source is loaded on each use, so subsequent source fixes do not need
  another menu installation.
- `text_menu_paste.js`: Signal/Firefox's replacement adapter uses the unique native Paste
  menu item (Cmd-V equivalent) through Accessibility, never simulated keypresses.
  It preserves every readable clipboard item/format, marks transformed text transient
  and concealed, limits it to the current host, and restores the clipboard after
  confirmed insertion. A newer user copy is preserved instead of overwritten.
  Selection, focus and clipboard content are rechecked before the single paste;
  the resulting draft must match the exact expected edit before success is reported.
  It then restores the transformed selection. No Copy or message-send action runs.
  `tests/menu_paste.test.cjs` covers restoration, concurrent copies, menu refusal,
  unverified writes and asynchronous draft confirmation. With `BTT_LIVE_TESTS=1`,
  it also exercises the real Cocoa bridge on a disposable named pasteboard; that
  opt-in test never accesses the general clipboard or user text.
  Command discovery inspects only immediate Cut/Copy/Paste siblings in a top-level
  menu; it never descends into browser history, bookmarks or Services submenus.
  Firefox bypasses `AXSelectedText` because its
  [setter](https://github.com/mozilla-firefox/firefox/blob/main/accessible/mac/mozTextAccessible.mm)
  passes a selection length to `DeleteText`'s end-offset argument, which can delete
  text outside the selection. The menu route checks the complete expected value and
  retains the selection for consecutive transforms.
  Temporary outcome diagnostics retain per-app results and the last error, so a
  successful Notes transform cannot erase evidence of a Firefox failure. App
  attribution uses AX keyboard focus; these private files contain no selected text.
- `text_markers.js`: Firefox reselection keeps native text-node positions. A flat
  offset can denote both the end of a paragraph and the start of the next one;
  restoring that offset can include an invisible paragraph separator. The adapter
  steps native character boundaries backward from the verified insertion caret,
  one UTF-16 unit per step, and selects the exact replacement without searching
  for text elsewhere or falling back to numeric selection writes. It checks focus,
  caret and text again before selecting. Marker positions are compared by flat
  index, node and trailing edge: Firefox's `AXUIElementForTextMarker` reports the
  previous node for any offset-0 marker (and a text field for its only node), so
  node identity alone cannot distinguish the two sides of a boundary, while the
  next character boundary of a trailing edge keeps the same flat index. Native
  `CFEqual` can return false for different range objects with identical endpoints.
  Before Paste, both selection endpoints must lie in the same native paragraph
  (`AXParagraphTextMarkerRangeForTextMarker`); a selection that spans a paragraph
  break, including one ending at the next paragraph's start, is refused because the
  flattened text would be pasted back merged. Selections across formatting runs
  within one paragraph, and whole-field selections in the URL bar, are allowed.
  `tests/text_markers.test.cjs` models those Firefox semantics from its
  accessibility sources, reproduces the refused paragraph-start selection and the
  URL-bar select-all, and verifies consecutive transforms, Unicode length changes,
  repeated text, document-end selections, boundary refusals and selection movement
  during reselection.
- `signal_text_diagnostic.js`: temporary Media > Tools > Signal Diagnostic button's
  target-app runner. It requests Signal activation and waits up to 30 seconds for a stable draft selection,
  optionally attempts the selected transform, then returns to BTT with a detailed
  Script Result. It uses live AX keyboard focus rather than cached workspace state;
  switching to Signal manually also works. Its inline options control the timeout and inspect-only
  mode. Reports include offsets, lengths and errors, never draft text, and are saved
  privately to `.local/signal-diagnostic-last.json`. The returned Script Result
  preserves the primary error even if returning focus or saving the report fails;
  `reportSaved: false` means the visible Script Result must be copied instead.
- `lib/text_transforms.js`: pure transform and selection-replacement policy.
  Lower/upper case preserve whitespace; snake/kebab/camel run per line through
  the existing `~/dev/case-anything/dist/cjs/index.cjs`. Base64 uses UTF-8; URI
  conversion retains `encodeURI`/`decodeURI` semantics. Decode errors leave the
  selection untouched. Replacement is plain text and app support/undo behavior
  must be checked in the destination app. Tests are in
  `tests/text_transforms.test.cjs` (`node --test` from this directory).
  `tests/text_transform_cases.cjs` shares examples with the explicit live smoke:
  `node tests/smoke_text_transforms.cjs --run`. That smoke creates temporary named
  triggers, updates their stored actions, checks all nine transforms in alternating
  order with synthetic text, then removes its triggers. It never edits user drafts
  and is not part of automatic test discovery. Signal/Notes editing still needs
  validation in the actual destination app.
  Repair backups live privately under this directory's ignored `.local/`.
  For existing Core JS actions, persist `BTTAppleScriptString` in
  `BTTAdditionalActionData`. BTT's API exports that field as `BTTScriptString`,
  but writing the exported name can leave the editor empty and the saved action
  inert. Disk verification checks the executable field without alias normalization.
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
- `tools_menu_item.py` / `install_tools_menu_item.js`: shared definition and guarded
  installer for the standard BPM, Rhythm, Convert Video, Interval Analysis and Temp
  Card Printer entries under Media > Tools. Their `install_*_menu.py` paths remain
  thin feature wrappers, including `install_interval_menu.py` and
  `install_card_printer_menu.py`.
- `install_performance_audio_menu.*`, `install_strip_metadata_menu.*`,
  `install_custom_conversion.*`, `build_performance_audio_menu.py`, `tools_menu_style.py`:
  installers and builders with distinct menu-tree or styling behavior.
- `play_status.js`, `recent_notes_content_script.js`, `btt_widget_spotify.scpt`:
  content-script sources retained in place. Notes' current provider is
  `../scripts/notes_menu.py`; screenshots use `../scripts/capture_btt_menu.zsh`.
- `btt_touchbar_folder_to_floating_submenu.py` and
  `create_floating_dropdown_from_submenu.py`: reusable offline conversion tools;
  inspect generated definitions before importing them.
- [Historical repairs](one_off_fixes/README.md): incident context for retired
  snapshot-specific fixes; executable versions are retained in Git history.
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
Behavior tests cover the maintained tools and their inspection/apply boundaries.

```sh
cd ~/dev/scripts
node --test macbook/tests/test_*.cjs
/usr/bin/python3 -B -m unittest discover -s macbook/tests -p 'test_btt_maintenance.py'
```
