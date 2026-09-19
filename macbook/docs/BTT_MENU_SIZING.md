# Repair conflicting Media menu sizes

`macbook/bettertouchtool/fix_menu_sizes.js` repairs explicit positive minimum
item dimensions that exceed their maximum dimensions, using BTT's scripting API.
It reads the complete Media tree through `get_trigger`, including nested menus.
It does not query a filtered child list or edit BTT's SQLite database.

On 2026-09-18, BTT 6.826 (2026091506) still had 64 conflicting widths:

| Items | Before (min/max) | After (min/max) |
| --- | --- | --- |
| 7 submenu buttons | 50 / 40 px | 50 / 50 px |
| 57 compact items | 60 / 40 px | 60 / 60 px |

All these items retain their existing 40-pixel heights. Valid custom widths,
including the Notes buttons, Tools button, and variable-width media status,
are preserved. The root menu dimensions, row break, item order, fonts, text,
actions, scripts, visibility, and padding are preserved. The valid separate
Notes/Performance Audio dropdowns are outside this repair's scope.

Screen capture was blocked in the agent session. Preserving the existing
minimum widths is a conservative fallback to the established button sizes;
exact visual equivalence cannot be confirmed. A button may change if BTT
previously resolved its contradictory bounds differently.

The earlier hang sample implicated main-thread UI observer bookkeeping.
These invalid bounds are worth fixing, but are not proven to cause that hang.

## Apply

```zsh
cd ~/dev/scripts
osascript -l JavaScript macbook/bettertouchtool/fix_menu_sizes.js
```

Before editing, the script writes and reads back a full private Media export
and a field-level repair plan in `tmp/btt-sizing-backup-<UUID>/backup.json`.
Directory permissions are 0700 and file permissions 0600. Backup failure or a
concurrent configuration change aborts the operation before updates.

It changes only the conflicting maxima using the persistent menu-property API
(`update_menu_item` with `persist:true`), and compares the complete resulting export with the expected
size-only change. It ignores only change timestamps/UUIDs and serialization
order of UUID-keyed collections. Unexpected changes are reported, not declared
successful. Repeating a successful repair performs no updates.

## Inspect or restore

Read-only inspection:

```zsh
cd ~/dev/scripts
osascript -l JavaScript macbook/bettertouchtool/fix_menu_sizes.js --inspect
```

For restoration, run the same script with `--restore` followed by the exact
backup path printed during the repair. This restores only changed maximum
dimensions, preserves unrelated later edits, and refuses to overwrite sizes
that were subsequently edited. It makes a new backup before restoring.
Restoration intentionally reinstates the old bounds, including their conflicts.

The source generator `btt_touchbar_folder_to_floating_submenu.py` now creates
valid 50×40 submenu buttons and 60×40 text items so future generated menus do not
reintroduce these default conflicts.

Validation: actual current DB metadata produces exactly 64 max-width patches;
9 repair/restore tests pass, and 38 relevant BTT menu tests pass overall.
JXA compiles. Live application requires Terminal because the agent's Apple Events
are sandbox-blocked and BTT's CLI socket server remains disabled.
