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
| `escape` | Escape dismissal while a Media dropdown is visible (Recent Notes, Pinned Notes, Performance Audio, Lighting Colour). |
| `text-keys` | Editing keys outside code editors: Cmd-X with no selection cuts the line and Cmd-C copies it; `'` `"` `` ` `` `(` `[` `{` wrap a selection. `--apply` installs, `--disable` turns them off; see below. |
| `finder-paste` | Cmd-V in Finder saves a clipboard image (a ctrl-cmd-shift-4 screenshot) as a PNG in the current folder; copied files and text paste as before. `--apply` installs, `--disable` turns it off; see below. |
| `repair rps` | Restore the missing sync-button link without running a deployment; optional `--with-escape`. |
| `repair sizes` | Repair conflicting min/max dimensions without normalizing valid custom sizes. |
| `repair paths` | Repair reviewed moved-script paths in active floating-menu actions and named triggers; `--apply` changes command fields only. |
| `repair actions` | Recover empty Media buttons from prior exports; `--apply` backs up, adds individual actions, and **restarts BTT to verify persistence**. |
| `repair dependencies` | Preview missing named dependencies; `--apply` copies unambiguous simple dependencies without enabling old presets, then verifies after restart. |
| `repair text` | Preview selection-only text transforms; `--apply` uses BTT's local socket, backs up, disables the old copy/delay actions, and replaces the paste actions. |
| `repair persistence` | Targeted speaker/Notes recovery; **restarts BTT when applied**. |
| `repair key-sequences` | Restore Master key sequences that an older per-version BTT store has and the current one lost; see below. |
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

Manual checkpoint promotion is explicit; replacing one also requires `--replace`.
There are two narrow exceptions: the scheduled monitor's edit acceptance (below,
under the service) and wired guarded installers, which finish their own apply by
checkpointing their verified edits. They first record a read-only audit and the
active checkpoint **before any BTT writes**. Only an initially healthy Guard with
an existing checkpoint is eligible. After the installer's saved-state verification,
they re-audit under `guard.lock`: the checkpoint must be unchanged, Guard must be
healthy or drifting, and every finding's UUID must belong to that installer's
actual edits. Capture still validates runtime exports against saved state. Pure
additions also get protected, even though their audit stays healthy; zero writes
never advance the checkpoint. `--no-checkpoint` disables this final step without
changing the installer's backup, edits or verification.

The final output is `BTT Guard checkpoint taken (...)`, `skipped (...)` with a
reason (for example, Guard had drift before the run or findings belong to another
edit), or `failed (...)` with a reason. Checkpoint failure never fails an already
verified apply. Apple Events export failure specifically asks for the owner's
Terminal. Every skip/failure also prints this manual command, to run only after
reviewing the menu and any unrelated drift:

```sh
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py guard checkpoint --accept-current --replace
```

The wired paths are:

- `repair_text_transforms.py` and the shared Tools installer (`tools_menu_item.py`
  / `install_tools_menu_item.js`), covering the BPM, Rhythm, Convert Video,
  Interval Analysis and Temp Card Printer wrappers. Tools workers return their
  exact written UUIDs; the launcher verifies saved button/action state before capture.
- `port_media_icons.py` (all three style scopes), `repair_media_actions.py`
  (actions and dependencies), `repair_script_paths.py` (ordinary and media-suite
  modes), `restore_media.py`, `stabilize_media.py`, and `repair_rps.py`.
- `btt.py style`, `restore-media`, and the corresponding `repair` commands forward
  `--no-checkpoint`; the installer owns the baseline, so dispatch never captures twice.

Path repairs whose disk saves are still pending print a checkpoint skip, not a
successful checkpoint. Initial drift still needs explicit owner review: these
installers do not retroactively bless earlier repairs or unrelated damage.

Not wired: Custom Conversion, Performance Audio, Strip Metadata, Notes, sizing
repair/restore and playback-status tuning currently verify API readback, not a
complete saved-state contract with an edit receipt. Their existing behavior is
unchanged. `build_performance_audio_menu.py` only generates files. Their edits,
like those of the unwired top-level installers (editing keys, Mac Panel), reach the
checkpoint through edit acceptance below. Guard's own `repair --apply` restores the
checkpoint and never replaces it.

The active pointer never advances during ordinary audits, BTT upgrades, failed
captures, or Guard's own repairs; only those two exceptions and an explicit
checkpoint move it. Prior generations are kept for 90 days: once a week the
scheduled monitor deletes inactive checkpoints and `rejected-captures/` entries
older than that (`btt_guard/retention.py`). The active checkpoint is never pruned,
however old, and nothing is pruned while the active pointer is unreadable.
`guard prune` previews what is due; `guard prune --apply` deletes it now.
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

Beyond the Media graph, Guard watches every top-level keyboard shortcut, key
sequence, named trigger, text transformer, menu-bar item (674, and the 681 script
item with its command, refresh interval, run-on-init and text-visible settings),
app-switch trigger and floating menu in each active preset, with their actions
(`WATCHED_TRIGGER_TYPES` in `btt_guard/database.py`). A lost or changed one is drift like a lost menu button,
and repair recreates it with its original UUID. Watched triggers are checked for
presence and state only: the named-trigger and empty-button checks stay with
Media, and a trigger's key binding (shortcut keys, typed sequence) is kept in the
recovery definition but not compared. A watched trigger the checkpoint lacks is
drift too (`added`), so edit acceptance protects a new trigger within 10 minutes; a
watched trigger linked to BTT's Trash and an app is a `multiple_scopes` finding
that blocks capture, since emptying the Trash deletes it. Touch Bar triggers (this
Mac has none), trackpad gestures and triggers with no app scope are not watched.

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
./macbook/bettertouchtool/btt.py guard service stop
./macbook/bettertouchtool/btt.py guard service start
./macbook/bettertouchtool/btt.py guard service uninstall
```

The optional per-user `com.jacobr.btt-guard` LaunchAgent runs once per minute in
the GUI login session, which checkpoint export needs for Apple Events access to
BetterTouchTool. It uses absolute interpreter/workspace paths and private logs.
This is a finite scheduled check, not a continuously running web app: it uses
`StartInterval`, not KeepAlive or an HTTP server. `prepare` only writes and lints
the plist. `install` writes the user's LaunchAgent and registers it without
sudo. `verify` requires a fresh completion heartbeat from the real registered
job. Do not call it installed merely because preparation or a foreground monitor
pass succeeded. `service stop/start/uninstall` controls only this matching
registration; state and checkpoints are retained. Uninstall before moving the
checkout.

Warnings go through `macbook/scripts/ntfy_warning.py`, which sources
`NTFY_WARNING_URL` privately from `pi/secrets/.bash_variables`.
The URL is never placed in process arguments, logs, plists or checkpoints.
Two identical unhealthy checks at least 60 seconds apart produce an alert;
successful alerts are deduplicated, failed deliveries retry after five minutes,
and two healthy checks produce one recovery notice. Alerts contain generic
finding counts only, not commands, labels, note titles or UUIDs. `last-audit.json`
is the private detailed report; a failed delivery does not erase the warning.
Malformed monitor state stays preserved while a separate recovery-state file
maintains bounded warnings and unavailable heartbeats. `guard notify-test --send`
sends one clearly labelled test; `guard monitor --no-notify` runs a local pass.

#### Edits made while BTT runs are accepted automatically

The scheduled monitor (`monitor --managed`, the LaunchAgent above) remembers the
BTT session of its last healthy audit: the main process id with its start time,
the BTT version and the active checkpoint. Drift that appears while that same
session is still running is treated as an edit (yours, an agent's or an
installer's). It shows as "edit pending" in the Mac Panel, sends no alert, and
once it has been unchanged for 10 minutes the monitor captures it as the new
known-good checkpoint. A further edit restarts the 10 minutes. Drift that first
appears after BTT relaunched or changed version (an update, a crash, a
launch-time sync), an unavailable audit, or drift with no recorded session goes
to the ntfy alert above and is never accepted. Capture keeps its own refusals
(empty active buttons, unresolved dependencies, disabled roots); a refused or
failed capture is alerted and not retried for the same drift. The first
acceptance may show a macOS prompt asking to let python3 control BetterTouchTool;
allow it, or that edit is alerted instead. Manual `monitor` passes never accept.
`guard acceptance` prints the pending and last accepted/refused change read-only;
state is in `.local/btt-guard/edit-acceptance.json`. Earlier checkpoints stay on
disk for 90 days (`guard checkpoint --list`), each with its full SQLite backup and
BTT export.

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

### Lost key sequences

```sh
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py repair key-sequences
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py repair key-sequences --apply
```

BTT keeps one store per installed version (`btt_data_store.version_*` in its
Application Support folder) and migrates the previous one on upgrade, so older
stores remain a source. The preview lists each top-level Master key sequence whose
typed keys no current trigger has, newest store first; of duplicates, the most
recently used copy wins. Trash-only, app-scoped, disabled and multi-action triggers
are listed for review instead. `--apply` backs up the current configuration, adds
each trigger with its original UUID through Apple Events (BTT's socket drops large
requests, such as a 94 KB paste), checks the runtime readback and waits for the
saved store. Run `repair_key_sequences.py --source <store>` directly to use one
store or a Guard checkpoint's `configuration.sqlite`.

In the 6.826 era BTT emptied its Trash and deleted every trigger linked to Trash
**and** Global, which a restore from Trash can leave behind (SHRG, L3K, JQQ, .PRY
and AQB were restored on 2026-10-31). The preview's `at_risk_trash_linked` lists
current triggers in that state. Guard now watches top-level key sequences (above),
so a lost one alerts and `guard repair --apply` recreates it from the checkpoint;
this restore covers sequences lost before a checkpoint held them.

### Typed-word triggers

Key-sequence triggers that expand a typed word (`lfd`, `ldev`) ignore keystrokes
sent by `osascript` / System Events `keystroke`: on 2026-11-01 `lfd`, which works
when typed by hand, stayed `lfd` in TextEdit. HID-level `CGEventPost` from a Swift
helper was built but never tried. So scripted typing can't test them; verify a
trigger through BTT's readback (`bttcli get_trigger uuid=...`)
and the saved store, check its condition with `text_editing_conditions.js`
(evaluates it with NSPredicate), then ask Jacob to type the word.

For the "Insert/Type/Paste Custom Text" action (118, BTT 6.885),
`BTTActionTextToPaste` must be RTF: with plain text, the first `ldev` deleted the
typed word and pasted nothing (2026-11-02). `ldev` now uses `lfd`'s RTF wrapper
with the text swapped; `COSNT…` and `SHRG` still store plain text.

`ldev` (UUID `77FDBD40-FC3E-542D-9125-39C84EF98B3B`) pastes `lionfeather.dev`
everywhere except terminal apps, where `ldev` is a zsh cd function. It exists
only in BTT's store; no script here builds it.

### Editing keys

```sh
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py text-keys
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py text-keys --apply
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py text-keys --disable
```

`install_text_editing_keys.py` adds nine global keyboard triggers and one
"Active App Did Change" trigger to the active Master preset, under fixed UUIDs:

- `'` `"` `` ` `` `(` `[` `{` typed over a selection wrap it and keep the original
  text selected inside the pair, so a second key wraps again (`("word")`).
- Cmd-X with no selection cuts the caret's line: its logical line (a paragraph,
  however it wraps) with the break that ends it, so the line is removed rather than
  emptied. An unterminated last line has no break; an empty last line is left
  alone. The runtime selects the line through Accessibility (a numeric range over
  `AXValue`; in Firefox, the caret's text-marker paragraph), but only while the
  caret and text are as it first read them, and reads the selection back. Only an
  exact match, with the text unchanged and the same app and field still focused
  (checked last, right before the press), is cut, by pressing the app's own Edit >
  Cut through Accessibility: BTT swallows a Cmd-X sent from its own Cmd-X trigger.
  Menu enable flags are not trusted: NSMenu validates them lazily, so a stale
  disabled Cut is still pressed and the text is the confirmation.
  It then checks that exactly the line left the text. No keystrokes are posted,
  since a posted key's delivery cannot be confirmed; if anything stops the cut
  first, the caret goes back unless you have moved it. In Firefox a blank line is
  refused with a notification: its flat text could not confirm the cut. A confirmed
  cut waits up to one second for the app's clipboard publication (a newer
  changeCount and matching line text). When its first item's string equals the line
  plus one `\n`, the runtime tags a copy of all the app's items, keeping RTF/HTML;
  otherwise it rewrites plain text to normalize the break or a leading non-text item.
  If the clipboard moves between that check and the write (some apps publish a cut
  in two steps), the tag is retried within the same one-second wait (elapsed time, so a
  slow clipboard read counts) while the
  clipboard still holds the line. A failed tag write restores the app's clipboard
  unless a newer copy arrived, and reports “Line cut, but Cmd-V will paste it
  inline.” under “Line cut with a warning”, never presenting the completed cut as
  a stopped cut; the recorded result adds the reason after that sentence.
- Cmd-C with no selection copies the caret's line (as VS Code does): its text and
  one `\n`, whatever break the app uses, on the last line too, so an empty line
  copies `\n` (in Firefox, the paragraph's text plus `\n`). The runtime writes it
  to the clipboard itself, as plain text plus the private pasteboard type
  `com.jacobr.editing-keys.line` on the same item, so the field is never selected or
  changed and the caret stays put; rich formatting is not copied. It notes the
  clipboard's version before reading the field and reads the caret and focus again
  after the text, and copies nothing if a key, a focus change or another copy came
  in between. If the write itself fails, the previous clipboard (every item and
  type) is put back. With a selection, or where the condition does not match,
  Cmd-C is the app's normal copy.
- Cmd-V with our tagged line copy and no selection inserts it above the current
  logical line only in an `AXTextArea`, leaving the caret on its original character
  (shifted by the inserted text's length). Single-line fields, search fields and
  URL bars pass through natively without running the script; copy/cut behavior there is
  unchanged. Empty and unterminated last lines are supported. The
  runtime collapses the selection at the line start, verifies ownership and
  readback, presses Edit > Paste once, and confirms the exact resulting text.
  Before a press, a refused paste restores the caret only if this run actually
  selected the line start and still owns it. After any attempted press, an
  unconfirmed paste leaves the caret where the press left it and says “check the
  text”: a busy renderer may still deliver that paste there. After a confirmed
  paste, restoring the original character caret is best effort. Firefox uses
  native paragraph-start markers and forward marker steps, not numeric writes;
  confirmation accepts the pasted break either omitted or exposed as a newline
  leaf. Blank-only Firefox pastes and unsupported AX text shapes fall back to
  normal Paste once and disarm the BTT variable (a copied blank line pastes inline
  in Firefox). Other pre-press refusals also fall back when the original
  focus and any owned selection can be confirmed; newer focus/selection is never
  overwritten. No fallback replays an attempted press. A missing Edit menu or
  failed runtime turns interception OFF, so the next Cmd-V passes through.
  With a selection, Cmd-V remains the app's normal paste (even with our tag).

The paste condition uses the persistent custom BTT string variable
`jacobr_editing_keys_line_clipboard`. Copy/cut first persist their start time in
`jacobr_editing_keys_line_copy_started_ms`, then set the line variable to `"1"`
before background `runJXA`. The paste action (never a condition) reads that
start time: only a paste within 1.5 seconds of a line copy/cut waits for its tag,
up to one second in 50 ms steps. Missing, invalid or older timestamps and failed timestamp reads do not wait.
This removes the extra one-second delay for an ordinary copy with a stale ON;
it does not guarantee ordering against a subsequent Return, because the fallback
still runs through background `runJXA` and an Edit-menu lookup. The stale ON lasts
until the next caret paste in an `AXTextArea`. A fallback always sets the line variable
OFF, even if the board still has a tag. The transport also sets it OFF on
missing/non-boolean results, malformed JSON and runJXA failures. BTT documents
[custom variables in conditions](https://docs.folivora.ai/docs/actions/conditions/)
and [set_persistent_string_variable](https://docs.folivora.ai/docs/1106_java_script.html), but
[standard variables](https://docs.folivora.ai/docs/1105_variables.html) offer no
pasteboard-type variable; live read-only probes of `clipboard_content_type` and
`clipboard_types` were empty. The tag is authoritative at runtime. A native copy
may leave the BTT variable stale: the next caret paste then performs normal
Edit > Paste once and sets the variable to `"0"`. Copy/cut/paste update it with
`set_persistent_string_variable`, so normal BTT restarts retain its binding.
`--apply` sets it to `"0"` and verifies it through `get_string_variable` before
any trigger is enabled; the app-switch action checks it before `runAppSwitch`
and persistently reasserts `"0"` if empty after a BTT reset/reinstall. Applying
therefore requires a fresh line copy/cut to enable whole-line paste again.
Clipboard managers must preserve the private type for it to remain a line copy.

Observed live 2026-11-05: an unbound condition variable caused BTT to swallow
Cmd-V without running the action or passing the key through. Foundation instead
silently treated a missing dictionary key as nil/false, so a false truth-table
result did not model that failure. `INITIAL_VARIABLES` is the one map initialized
and read back by the installer. Tests check every installed predicate against
that map and BTT's built-ins. The paste predicate checks the app, then the variable,
then `focused_element_details`, avoiding a focused-element dump for an untagged
paste. The evaluator walks the parsed predicate
and rejects cases missing any referenced variable, including branches that would
short-circuit. Unsupported expression kinds fail loudly rather than bypassing
that validation.

The explicit disjoint-UUID pair in `editing_key_ids.py` (shared by both installers) allows the Finder paste
trigger (non-text focus) beside this text-only Cmd-V trigger; all other shortcut
conflicts still refuse installation, in either installer. BTT's
[conditions docs](https://docs.folivora.ai/docs/actions/conditions/) allow identical
shortcuts with different conditions, while its
[JSON reference](https://docs.folivora.ai/docs/2002_trigger_json.html) documents
per-trigger pass-through on mismatch. Neither specifies whether a mismatching
trigger passes through while another matches: single-paste coexistence needs a
live check in Finder and a text field.

Live checks still owed to Jacob (offline fixtures are not proof of BTT delivery):
- Chrome and Firefox contenteditable fields must expose `AXTextArea` for whole-line
  paste; if the role is different, line paste simply does not apply there.
  Confirm the caret stays on its character with both ordinary and quick copy/paste.
  In Firefox also paste a rich HTML line cut and verify it creates a separate
  paragraph rather than merging inline (flat AXValue alone cannot prove this).
- In Messages, make an ordinary copy after a line copy, wait over 1.5 seconds,
  then press Cmd-V followed immediately by Return: paste must precede send.
- Confirm search/URL fields paste normally; check Cmd-V with a selection and in VS Code.
- Finder must paste only once, with both native data and a stale tagged clipboard.
- After a BTT relaunch, normal Cmd-V must still work; with nothing focused (for
  example Preview), the key must pass through rather than be swallowed.
- Verify Cmd-X and its warning notification in TextEdit. No live keypress, focus
  changes or general-pasteboard probes were performed for these review fixes.

Notifications retain BTT's documented unnamed body parameter (`""`), not `text`:
[CLI command reference, display_notification](https://docs.folivora.ai/docs/scripting/cli/).
The payload keys are correct; delivery/visibility of the reported TextEdit error
has not been verified live.

Each keyboard trigger has an advanced condition and BTT's "If advanced conditions
fail, let the shortcut pass through" option: the key behaves normally unless
`focused_element_details` shows a settable `AXSelectedText` with a nonempty range
(wrapping) or an empty one (Cmd-X, Cmd-C, tagged Cmd-V, with `AXTextArea` also
required for Cmd-V), in an app outside
`text_editing_conditions.EXCLUDED_APPS` (code editors
and terminals, plus `com.jetbrains.*`). Read-only text, web page text and apps with
no text tree never match. Earlier condition checks measured about 20 ms per keystroke; ordering with a
background paste still needs the Messages live check above. The installer evaluates all three
conditions against `text_editing_conditions.CASES` with Foundation's NSPredicate
before any write.

Wrapping reuses `text_selection.js` (Firefox and Signal replace through Paste, as
for the text transforms). Electron apps build their text tree only on request, so
the app-switch trigger runs `runAppSwitch` to set `AXManualAccessibility`
in the newly focused app. A line is the logical line in every app, Firefox
included, so a soft-wrapped line is cut or copied whole. Wrapping replaces
the selection as plain text, which drops formatting in rich editors. Multiple
cursors and "select next occurrence" are not possible: macOS text controls have a
single insertion point.

`--apply` backs up to `.local/text-editing-key-backups/`, creates any missing or
changed trigger disabled, verifies the API readback and the saved store, then
enables all of them and advances the Guard checkpoint. It refuses when another
enabled shortcut in an active preset uses one of these keys, except the explicit
Finder/line-paste pair above.

### Finder paste

```sh
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py finder-paste
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py finder-paste --apply
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/btt.py finder-paste --disable
```

Finder's own Paste handles only copied files, so a screenshot taken to the clipboard
(ctrl-cmd-shift-4) pastes nowhere. `install_finder_paste.py` adds one global Cmd-V
keyboard trigger to the active Master preset, under the fixed UUID
`EDA649C3-026B-4402-9BF9-CE339233EFB2`. Its runtime, `finder_paste.js`, looks at the
pasteboard in this order and never writes it:

- File URLs: Finder's own Edit > Paste. Files come first because Finder also puts a
  copied file's icon on the pasteboard as TIFF.
- An image: saved as `Screenshot YYYY-MM-DD at HH.MM.SS.png` (local time; ` 2`, ` 3`
  and so on when the name is taken) in Finder's insertion location, the folder New
  Folder would use (the Desktop when no window is open), then selected. A Recents or
  search window has no such folder, so there it stops with a notification, as New
  Folder is unavailable there. A `public.png`
  is written byte for byte; anything else NSImage reads (TIFF, JPEG, HEIC, PDF) is
  converted. The file is written under a hidden name and renamed, never over an
  existing file. In a Get Info window, where a pasted image sets a custom icon, and
  on any text focus the condition missed, Edit > Paste is pressed instead.
- Anything else: Edit > Paste, which does nothing when Finder has nothing to paste.

Paste is pressed through Accessibility, as the editing keys press Cut: sending Cmd-V
from the Cmd-V trigger would be swallowed the way Cmd-X was. The trigger's advanced
condition (`finder_paste_condition.CONDITION`), with BTT's pass-through option, hands
Cmd-V straight to the app unless Finder is frontmost, and to Finder when
`focused_element_details` shows a settable `AXSelectedText`: rename fields, the
search field, Go to Folder and Get Info's text fields. The trigger is global because
BTT 6.990's socket `add_new_trigger` filed it under Global despite its
`BTTAppBundleIdentifier` (the Apple Events form has no app parameter either); the
Finder check comes first in the condition. BTT has no condition variable for the
pasteboard's types (its Clipboard variables only report the clipboard manager, and
`clipboard_content` is text), so every Cmd-V on a non-text focus in Finder runs the
script, which sorts files, images and text itself.

`--apply` backs up to `.local/finder-paste-backups/`, checks the condition against
`finder_paste_condition.CASES` with NSPredicate, creates the trigger disabled,
verifies the API readback and the saved store, then enables it and advances the
Guard checkpoint. It refuses when another enabled shortcut in an active preset, in
any app, uses Cmd-V. The install flow is `trigger_install.py`, shared with the
editing keys. `tests/test_finder_paste.py` runs the runtime against a named
pasteboard and a temporary folder (with `BTT_LIVE_TESTS=1`, also inside BTT);
`tests/finder_paste.test.cjs` checks its Finder decisions with Accessibility and
Finder scripting stubbed.

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

`repair_script_paths.py --sonos --inspect` audits saved Sonos commands, including
inactive legacy copies. `--sonos --apply` uses the existing guarded field updater
and configuration backup to replace old launcher paths, source-style invocation,
reviewed spelling errors and obsolete command names. It validates each unique
argument list with `sns --check`; it never executes a Sonos operation. Named-trigger
references such as `sns group down` keep their names; their actual shell targets
are repaired. Explicit SSH actions keep `--pi` intent. Run the apply command from
Terminal if Apple Events are unavailable in an agent shell.

The updater explicitly preserves `BTTEnabled`/`BTTEnabled2`: BTT can otherwise
re-enable disabled Touch Bar buttons during a command-only patch. Saved-state
verification includes `ZENABLEDNEW`. If an older repair stopped after applying
commands because it re-enabled a disabled button, inspect the original private
backup with `restore_path_enabled.py <backup>/plan.json`. Its `--apply` restores
only accidental disabled-to-enabled drift, checks the full runtime snapshot,
then checks saved commands and enabled states. It refuses other drift and never
replays Sonos commands or promotes a BTT Guard checkpoint.

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
It does not rebuild the app or move data. After saved verification it uses the same
narrow installer-checkpoint gate described above (`--no-checkpoint` opts out).
Move data only after checking processes and job state; preserve custom presets
and report identities. Recreate venvs rather than moving them.

### Sources

- `btt.py`: maintained CLI; fixed argument-list dispatch, no shell interpolation.
- `repair_text_transforms.py/.js`: guarded installer for the nine existing text
  transform buttons. `btt_socket.js` (and `btt_socket.py` for the Python installers)
  calls BTT's bundled CLI through its local socket, avoiding Apple Events for
  configuration changes. Enable Command Line /
  Socket Server once in BTT's scripting settings and restart BTT before applying.
  The socket is restricted to the owning user; no network webserver is needed.
- `install_text_editing_keys.py`: the editing keys' guarded installer, also through
  the socket CLI (see Editing keys). `text_editing_conditions.py` holds their trigger
  conditions and truth table, evaluated with NSPredicate by `text_editing_conditions.js`.
  `text_editing_keys.js` is their runtime (wrap, line cut and copy, app-switch preparation),
  loaded after `text_selection.js`; each trigger's action reads both files.
- `text_selection.js`: runtime Accessibility adapter, executed inside BTT via
  `runJXA`. It reads the focused control's selected text/range, checks that the
  selection is writable and unchanged. Native editors use `AXSelectedText`; Signal and Firefox
  (every `org.mozilla.*` bundle, including Developer Edition) delegate replacement to
  `text_menu_paste.js`. `write` can leave part of the replacement selected (wrapping
  keeps the inner text selected). `runSelectionAction` gives every entry its
  `{ok, result|error}` outcome and the opt-in diagnostics. Unsupported controls or missing
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
- `text_menu_paste.js`: `findEditMenuItem` locates the Cut/Copy/Paste group (the
  editing keys press its Cut), `focusUnchanged` is the focus check both Paste and
  the line cut repeat before they act, and `writeClipboardText` is the one plain
  clipboard write, used by Paste's temporary clipboard and the editing keys' Cmd-C:
  it refuses if the clipboard moved since `copyPasteboard` saved it, and on a
  failed write `restorePasteboard` puts the saved clipboard back while the failed
  write is still the newest.
  Signal/Firefox's replacement adapter uses the unique native Paste
  menu item (Cmd-V equivalent) through Accessibility, never simulated keypresses.
  It preserves every readable clipboard item/format, marks transformed text transient
  and concealed, limits it to the current host, and restores the clipboard after
  confirmed insertion. A newer user copy is preserved instead of overwritten.
  Selection, focus and clipboard content are rechecked before the single paste;
  the resulting draft must match the exact expected edit before success is reported.
  It then restores the transformed selection. No Copy or message-send action runs.
  `tests/menu_paste.test.cjs` covers restoration, concurrent copies, menu refusal,
  unverified writes and asynchronous draft confirmation, on the in-memory
  pasteboard in `tests/pasteboard_fixture.cjs` that the editing keys' tests share.
  With `BTT_LIVE_TESTS=1`, it also exercises the real Cocoa bridge on a disposable
  named pasteboard; that opt-in test never accesses the general clipboard or user
  text.
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
- `text_markers.js`: Firefox reselection keeps native text-node positions (its
  `paragraphAt` ranges are also the editing keys' Firefox lines). A flat
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
  `tests/firefox_editor_fixture.cjs` models those Firefox semantics from its
  accessibility sources for both `tests/text_markers.test.cjs` and the editing keys'
  `tests/editing_keys.test.cjs`. The first reproduces the refused paragraph-start
  selection and the URL-bar select-all, and verifies consecutive transforms,
  Unicode length changes, repeated text, document-end selections, boundary refusals
  and selection movement during reselection.
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
  selection untouched. `wrap` surrounds a selection with one of `pairs`.
  Replacement is plain text and app support/undo behavior
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
  atomic file writes/locking. `installer_checkpoint.py` owns the healthy-baseline,
  unchanged-checkpoint and touched-UUID gates plus the nonfatal installer result;
  its fixed subprocess bridge lets standalone installers use the package without
  import-path shims. Guard's own repair does not use this promotion path.
- `install_mac_panel_menu.py/.js`: guarded installer for a standalone Mac Panel
  menu-bar item (type 681, shell-script title: SF Symbol `slider.horizontal.3` plus the
  whole-machine CPU busy % from `macbook/scripts/cpu_percent.py`, refreshed every 3 s;
  the 681 item has its own UUID, added and verified (runtime and saved, including
  its script settings in the trait's `ZBEZIERPATH`) before the old icon-only 674
  item (`AF69261A-...`) is deleted, so a failure never leaves the menu bar empty and
  a rerun finishes the job; `--no-checkpoint` skips the Guard checkpoint) and All Apps keyboard
  shortcut (installed as `ctrl+opt+cmd+P`), both using BTT's built-in Open URL
  action for `http://127.0.0.1:8769/`. BTT saves each trigger's notes as its
  description (`ZDESC`) and ignores a button name on menu-bar items. Preview is the
  default; from the repo root:

  ```sh
  /usr/bin/python3 -B macbook/bettertouchtool/install_mac_panel_menu.py
  /usr/bin/python3 -B macbook/bettertouchtool/install_mac_panel_menu.py --apply
  ```

  Selects the first free `ctrl+opt+cmd+P`, `ctrl+opt+cmd+M`, or `ctrl+opt+cmd+K`
  (including disabled/inactive saved shortcuts when checking conflicts). Fixed
  UUIDs update in place; ambiguous markers, foreign scopes, children and conditions
  are refused. Applying takes a private SQLite/plan backup, checks for concurrent
  edits, updates only these two top-level Master triggers, then verifies runtime
  and saved configuration. No restart, browser launch or action execution occurs.
  Uses the existing local socket (`btt_socket.js`), not Apple Events; read-only
  preview works from an agent shell when BTT's Command Line / Socket Server is
  enabled. Apply is always explicit. These top-level triggers are outside the Media
  graph but watched by Guard; the installer does not checkpoint them itself, so edit
  acceptance picks them up. Existing Guard drift must be reviewed separately; do not
  accept it just to add Panel.
- `install_lighting_menu.py`: guarded installer for Media > Lighting, a 💡 submenu
  appended to the Media bar holding `<<`, a two-line Solder SW button (the Screen Cap
  button's label form) that runs `macbook/scripts/van_lighting.py toggle
  switch.solder_flood`, and 🎨, which shows the hidden top-level "Lighting Colour"
  dropdown (positioned like the Notes dropdowns). Its one Web View item (type 778,
  `BTTMenuItemWebViewPlain` 0 so BTT's script bridge stays) is the page
  `lighting_control/page.py` builds for the WiZ bulbs; the page runs `van_lighting.py
  lights`/`set` through `runShellScript` and never calls the dashboard directly.
  It runs on the shared `media_submenu.py` launcher and `media_submenu_install.js`
  worker. The installer owns the structure (a flat list of items, each naming its
  parent, `null` for a top-level menu, plus the noun, title and backup prefix the
  worker's messages use); the worker indexes the Media tree and the owned dropdown once
  per read. Preview is the default; `--apply` backs up the trees, writes only its own
  UUIDs, verifies runtime and saved records (including each action's Master preset link),
  then checkpoints Guard; `--refresh` redraws the bar. Moved, retyped or disabled items
  are refused. Existing items are restyled with `update_menu_item` and their actions
  replaced one at a time: a full `update_trigger` saves new actions without their preset
  link, and a rerun repairs any such action. Re-run `--apply` after editing
  `lighting_control/`: the page is stored inline in BTT. Hand reorders and items added by
  hand to the submenu or dropdown survive.

  ```sh
  /usr/bin/python3 -B macbook/bettertouchtool/install_lighting_menu.py --apply --refresh
  ```
- `install_mac_menu.py`: guarded installer for Media > Mac, a submenu shown as the SF
  Symbol `laptopcomputer` (the Sonos join button's icon settings), appended after
  Lighting, for Mac window and system items. It holds `<<`, iTerm↑ and iTerm↓ (two-line
  labels like Solder SW), which run `macbook/scripts/iterm_tile.js external` and
  `builtin`: every iTerm window that is neither a hotkey window nor minimized moves to
  the largest external monitor or the MacBook display and is tiled side by side, each
  full height, together filling the visible width. Windows already on that display are
  re-tiled and keep their left-to-right order; arrivals join on the right. With no
  external monitor it posts a notification and moves nothing; iTerm is never launched.
  `iterm_tile.js external --dry-run` prints the plan. Same shared launcher, worker and
  flags as Lighting.

  ```sh
  /usr/bin/python3 -B macbook/bettertouchtool/install_mac_menu.py --apply --refresh
  ```
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
