# Recent Notes and Pinned Notes in BTT

`macbook/bettertouchtool/install_notes.js` repairs the two existing Media buttons
in place and gives each a separate vertical floating menu. Main-menu order and
button UUIDs are preserved. The button titles are static; only the dropdown runs
a content script. Dropdowns refresh when opened, with no periodic background poll.
Dropdowns open near the mouse, and each show action resets their position/size.
Opacity is explicit in both active/inactive states.

The shared `notes_menu.py` generator explicitly styles every note row: charcoal
`#2B2D31`, off-white text/icons `#EEEDE9`, and a subtle hover background `#36393F`.
Light and dark appearances use the same palette; empty/error rows match too.
Full BTT item-style properties are emitted alongside Simple JSON title/action
fields, as [documented by BTT's author](https://community.folivora.ai/t/floating-menu-w-content-script-text-formating-issue/44210/3).
This is runtime styling: reopen either dropdown to refresh it. No BTT import,
restart, layout change, or extra hover actions are needed.

Recent Notes lists the ten most recently modified notes. Pinned Notes lists the
notes pinned in Apple Notes, newest modified first. Deleted notes, notes in
Recently Deleted, and incomplete records without folders/titles are excluded.
The current data has 23 valid pins. This count is not hardcoded.

Each note opens by its Notes object ID using `macbook/scripts/show_note.sh`,
avoiding title ambiguity and replacing the stale `sh/show_note.sh` action.
The old hardcoded Guitar Songs item and old Back rows are retired; the installer
backs them up with the full original Media menu before changing BTT.
The two dropdown containers deliberately appear in BTT's top-level menu list;
their launch buttons remain inside Media. "Items in Group: 0" is expected for
these containers because their rows are generated at runtime.

If an earlier installer left disabled Back/GuitarSongs rows at the top level,
rerun this installer. It includes those three exact legacy rows in a new backup
and removes them only after verifying the working replacements. Other root
menus/items are preserved.

## Setup

AppleScript does not expose Notes' pinned flag. `macbook/scripts/notes_menu.py`
therefore reads only titles, folder metadata, modification times, and IDs from
the local Notes SQLite store, in read-only mode. It never reads note bodies or
changes Notes. Its schema checks fail explicitly after incompatible macOS changes.

**BetterTouchTool needs Full Disk Access.** The macOS permission switch must be
enabled manually; a shell command cannot grant it. Open the correct pane:

```zsh
open 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'
```

Enable BetterTouchTool. If macOS requests a restart, use its Quit & Reopen button
or quit/reopen from Terminal:

```zsh
osascript -e 'tell application "BetterTouchTool" to quit'
open -a BetterTouchTool
```

Then install from Terminal (the agent sandbox cannot send BTT Apple Events):

```zsh
cd ~/dev/scripts
osascript -l JavaScript macbook/bettertouchtool/install_notes.js
```

The script checks Notes access from Terminal, exports the original Media menu,
writes it to a private `tmp/btt-notes-backup-<UUID>/backup.json`, reads the backup
back, and only then updates BTT. Both dropdowns are verified before switching
the existing buttons. Rerunning is safe and preserves the current button order.
The old dropdown configurations, if present, are included in the backup too.
The installer then opens both actual launchers and checks BTT's visible-menu
variable plus a successful content-script result. It leaves Recent Notes open.
These checks stop as soon as ready (maximum five seconds per menu); invisible
menus and content failures are reported as errors rather than installation success.
Terminal access does not prove BTT access; if a dropdown reports an access error,
check BTT's Full Disk Access permission and restart it.

If BTT requests permission to control Notes when opening a row, allow it: the
existing note-opening helper uses Notes' scripting interface to display the note.

## Diagnostics

To apply just the compact main-bar labels (14pt Recent/Pinned/Tools/Find My,
both Notes buttons on two lines), with a verified backup and no action changes:

```zsh
cd ~/dev/scripts
osascript -l JavaScript macbook/bettertouchtool/install_notes.js --labels-only
```

Read-only configuration/data check:

```zsh
cd ~/dev/scripts
osascript -l JavaScript macbook/bettertouchtool/install_notes.js --inspect
```

Metadata checks (counts only, no titles or note contents):

```zsh
cd ~/dev/scripts
/usr/bin/python3 -B macbook/scripts/notes_menu.py recent --check
/usr/bin/python3 -B macbook/scripts/notes_menu.py pinned --check
```

Backups contain the complete original menu, which can contain sensitive action
payloads. Keep them local. To restore, use the backup's two original note-button
definitions through BTT's API; do not paste the complete Media menu over the live
one or directly edit BTT's database. The new dropdowns are independent and can
be disabled after restoring the original buttons.

Validation: metadata queries against the live Notes store; fixture tests for
pinning, recent sort, deletion/trash/orphan filtering, correct object IDs, shell
quoting, and schema changes. Mocked BTT tests check backup-before-write, fixed
titles, vertical menus, preserved siblings/order, idempotence, and failures.
Actual BTT UI and note opening require the user's Terminal/GUI session.
