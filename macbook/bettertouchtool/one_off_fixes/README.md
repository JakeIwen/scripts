# BetterTouchTool one-off fixes

These scripts preserve the recovery work from the July 19, 2026 BetterTouchTool
floating-menu incident. They are dated and deliberately specific to the BTT
database snapshots, UUIDs, and application build involved in that incident.
They are reference material, not a general BTT maintenance interface.

## Incident summary

The `Media` floating menu accumulated two records with the same embedded menu
identifier and Control-Option-Command modifier chord. BTT could consequently
show stale menu content or fail to register the shortcut after a reload. JSON
clipboard imports also exposed a BTT 6.011 table-index bug and crashed the app
before the imported records were committed.

Recovery restored the July 18 snapshot, repaired its known duplicate/order
state, grafted seven completed July 19 additions from the preserved database,
and finally changed the stale menu's embedded identifier and modifier keys so
it could no longer collide with the current menu.

## Script index

### `2026-07-19-quarantine-duplicate-media-modifier-collision.zsh`

The final shortcut fix. It targets the exact current and stale Media UUIDs,
requires both embedded identifiers to still equal `Media`, then gives the stale
record a unique identifier, clears its modifiers, and disables it. It backs up
the database first and deletes nothing. It is not a generic collision fixer.

### `2026-07-19-repair-media-duplicate-and-order-gaps.zsh`

Repairs the earlier known database shape: detaches and disables the exact stale
Media record, restores the exact Display Power record if present, and compacts
the current Media children's indexes to a contiguous range. It accepts a copied
database path for staging/testing but otherwise targets the fixed 6.011 build.

### `2026-07-19-restore-btt-snapshot-and-repair-media.zsh`

Restores the exact snapshot at `~/backups/BetterTouchTool/reorg/btt1` through a
staging directory, invokes the paired duplicate/order repair above, and moves
the displaced live BTT support directory to a timestamped safety location.

### `2026-07-19-graft-media-additions-from-pre-restore-database.zsh`

Copies the seven known additions (Emoji, addr, Sidecar, Spktv, Night Shift,
Display Power, and Recent Notes) and all their descendants from the exact
preserved pre-restore database. It expects 86 copied records and a final Media
menu containing 25 children numbered 0 through 24.

### `2026-07-19-CRASHES-BTT-single-item-json-paste-reproducer.zsh`

Archived failed import approach. It copies one generated Media item to the
clipboard without a root `BTTOrder`. Pasting it caused BTT 6.011 to crash with
an out-of-bounds table index. Do not use it to install menu items.

### `2026-07-19-CRASHES-BTT-batch-json-paste-reproducer.zsh`

Archived failed import approach. It combines all seven generated items and
removes their root `BTTOrder` values before copying them. Pasting the result also
crashed BTT 6.011. Do not use it to install menu items.

## Before adapting any recovery script

- Quit BetterTouchTool completely before touching its live Core Data database.
- Keep BTT cloud sync disabled so stale records cannot be restored afterward.
- Take a fresh, verified backup of the entire BTT Application Support folder.
- Re-discover the current database filename, schema, menu UUIDs, parent links,
  embedded identifiers, modifier values, and order state.
- Test against a copied database and run `PRAGMA integrity_check` before and
  after the change.
- Do not copy Core Data rows between unrelated schemas or database lineages.

Useful reusable tools could be derived from this work: a read-only duplicate
identifier/modifier auditor, a guarded contiguous-order repair, and a subtree
export/import tool that validates schemas and remaps primary keys. None of the
scripts in this directory currently provides those general guarantees.
