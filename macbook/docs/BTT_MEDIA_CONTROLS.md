# Media controls recovery and diagnostic screenshot

## Black main-bar text and original Sonos icons

```zsh
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/port_media_icons.py --main-style
```

This mode colors the text of every direct Media button/submenu launcher black,
including hover and dark appearances. Native macOS attributed-text conversion
preserves the characters, fonts, point sizes, alignment, and line breaks instead
of rebuilding the labels. The center play/status button also gets an explicit
black SF Symbol tint, including hover/dark states. Playback-status text inherits
the black text style when refreshed; its content script and icon shape/size are
unchanged. Color emoji retain their native artwork.

It also installs the original Touch Bar image assets for the four main speaker
controls: four arrows pointing inward for Sonos join, four pointing outward for
unjoin, and the old volume-down/up speaker icons, all tinted black. Only icon/text
appearance fields are changed. Fonts/sizing bounds, padding, actions, order,
backgrounds, spacing, modifier behavior, and the main menu's position are retained.
Nested submenu rows and independent dropdowns are not recolored; Notes dropdowns
keep their charcoal/off-white palette.

Like the transport-only mode below, it makes a full verified configuration backup
first, checks the full Media tree for unintended changes, and verifies saved
properties. Add `--inspect` for a read-only plan. No import or restart is needed.

The partymode/join button is explicitly centered: its old asymmetric horizontal
padding (left 8, right −9) is reset to zero on both sides, with centered icon
placement and no image offset. Its 40×40 sizing bounds are not changed. This is
the one intentional padding adjustment in `--main-style`.

BTT may convert embedded images (icon type 1) into PNG preset files (type 7).
The updater accepts that conversion only after decoding and comparing image
dimensions and pixels: alpha masks when both configurations tint the image,
full RGBA otherwise. It restricts file reads to the verified preset bundle.
Other configuration fields and action trees are still checked strictly. This
avoids false readback errors and repeated reinstallation of unchanged icons.

To update only the center play/status icon and text to black (without revisiting
the speaker icons or other labels):

```zsh
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/port_media_icons.py --status-style
```

## Original Touch Bar transport icons

```zsh
/usr/bin/python3 -B ~/dev/scripts/macbook/bettertouchtool/port_media_icons.py
```

The original Touch Bar entries still contain monochrome PNG/TIFF icon data.
This updater reads those records without changing them, backs up the full BTT
database and Media export, and replaces the eight stop/resume/previous/seek/next
emoji labels with the saved artwork. Floating-menu image mode tints the artwork
black in both light and dark appearances, with matching black captions. No external icon files are required
after installation: the original bytes are embedded in the item configuration.

Five-minute seeks were long-press actions on the original rewind/forward buttons,
so they have no separate artwork. Their floating buttons reuse the arrow icon
with a small `5m` caption underneath; short seeks get `20s` below the arrows.
The current VLC named triggers use offsets of ±20,000,000 microseconds (20 seconds).
Tooltips
identify every action. The main play/status widget already uses a native symbol
and is left alone, as are power, volume, and the rest of the menu.

Only icon, label, and tooltip appearance properties are updated through
`update_menu_item(..., persist:true)`. Actions, button sizing bounds/padding,
identifiers, order, colors, row spacing, and drag/modifier behavior are preserved.
The complete exported tree is checked for unintended changes, and saved icon
properties are verified in SQLite. No restart, import, or media action is run.
`--inspect` lists the proposed buttons without contacting BTT's scripting API.

## RPS link and Escape dismissal

```zsh
cd ~/dev/scripts
/usr/bin/python3 -B macbook/bettertouchtool/repair_rps.py --with-escape
```

The RPS repair restores only its missing `Sync RPi Scripts` action, preserving
the original action UUID. It verifies the named trigger resolves uniquely in
active presets and points to `pi/sync_scripts.sh`. It creates the action as an
individual child record, never replacing the Media tree. Full SQLite and API
exports are backed up privately first, then the API and saved database are checked.
It **does not press RPS, run the sync, deploy files, or restart BTT**.

`--with-escape` also installs one plain-Escape keyboard shortcut. Its advanced
condition limits it to when Recent Notes, Pinned Notes, or Performance Audio is
visible. The action hides all three without activating a hovered item; it leaves
the main modifier-held Media bar alone. When those dropdowns are hidden, the
original Escape event passes through to the foreground app. The installer rejects
an existing active plain-Escape binding rather than replacing it. It creates the
new shortcut disabled, validates the condition with macOS Foundation and verifies
the saved settings, then enables it. This addition has its own full configuration
backup. Future unrelated dropdowns are not automatically included.

Omit `--with-escape` to restore RPS alone. Add `--inspect` for read-only checks.
The agent cannot exercise actual keyboard dispatch from its sandbox; saved
configuration checks do not represent a live sync or physical-key test.

## Playback-status cleanup and compact row spacing

```zsh
osascript -l JavaScript ~/dev/scripts/macbook/bettertouchtool/tune_media_status.js
```

This makes a verified private full Media backup before two persistent style
updates: installs `play_status.js` into the existing PPause content-script setting,
and reduces the main menu's vertical spacing from 5 to 2.5. It preserves actions,
submenus, positions, sizes, horizontal spacing, and the working drag/modifier
settings. Reruns do not halve the spacing again. No restart or menu import.

The status query suppresses only this display query's remote stderr (including
curl progress/errors from the idle-player fallback). Idle status is blank;
failed SSH queries become `Status unavailable`. Valid playback
title/time output is preserved. Queries use the verified LAN hostname, SSH batch
mode, a connection timeout, and keepalive failure limits. No Pi files are changed.
`--inspect` reports the proposed changes without writing a backup or modifying BTT.

## Persistent submenu recovery

For the current persistence repair and screen/content layout, run from Terminal:

```zsh
cd ~/dev/scripts
/usr/bin/python3 -B macbook/bettertouchtool/stabilize_media.py
```

This supersedes the earlier nested-JSON recovery command below. That command's
API readback was not sufficient to establish that restored descendants survived
a restart. The new Python/JXA workflow:

- Plans each missing submenu row and action as an individual `add_new_trigger`
  operation with its own UUID and explicit parent. Existing records are checked
  for their saved parent, preset, type, and action before any writes.
  Compact action-only exports are normalized into floating-menu action records
  (including the trigger class); the original action payload and UUID stay intact.
- Backs up the complete SQLite configuration through SQLite's consistent backup
  API, as well as the full Media/dropdown exports and repair plan, under
  `tmp/btt-persistent-backup-*`. The live database is read-only to the helper.
- Saves appearance changes with `update_menu_item(..., persist:true)`.
  Reruns skip records and layout keys that are already correctly saved, but still
  verify the complete expected configuration after restarting.
- Anchors Media to the top-left of the screen containing the pointer, not the
  focused window. Uses content sizing for Media and the two Notes/Performance
  Audio dropdowns, capped by the usable screen dimensions.
  Preserves existing drag handling: setting `BTTMenuDisableDrag=1` broke normal
  held-modifier clicks on this BTT 6.826 installation. The user confirmed that
  reverting only this setting to `0` restored clicks. The stabilizer no longer
  overrides it; top-left positioning remains configured independently.
- Quits and reopens BTT normally (never force-quits), then checks the saved
  database again and exercises both Notes launchers. It does not claim success
  based only on BTT's in-memory trigger export.

The script restarts BTT as part of this test. If native content sizing still
does not produce the desired dropdown height, the user's requested fallback is:

```zsh
cd ~/dev/scripts
/usr/bin/python3 -B macbook/bettertouchtool/stabilize_media.py --full-height-dropdowns
```

This uses 100% of the usable screen height for the three standalone dropdowns;
Media itself remains content-sized. `--inspect` checks the plan without changing
configuration or restarting BTT. A repair that fails persistence checks reports
the missing/incorrect record IDs and its backup location.

## Earlier recovery helper

The installer backs up the current complete Media menu to a private
`tmp/btt-controls-backup-<UUID>/backup.json` before applying any changes.
It then:

- Recovers missing rear-speaker and Sonos submenu descendants/actions from the
  newest complete saved Media exports. Current labels, top-level positions, and
  existing custom descendants are retained. Restored invalid widths are corrected.
- Restores the existing Recent/Pinned Notes Show Floating Menu actions and keeps
  their two-line 14pt labels. Width bounds become 54–78px (40% below 90–130).
- Adds one **Screen Cap** button at the end of Media. It is configured not to
  close the menu on click and runs no synthetic keyboard shortcuts.
- Verifies restored descendants, action payloads, widths, and existing order;
  invokes the two Notes launchers to check visibility/content readiness.

The source backups stay intact. All updates use complete item exports with
explicit parents, rather than assuming that partial updates preserve children.
Rerunning merges missing definitions without duplicating the capture button.
`--inspect` prints the plan and recovery-source paths without changes.
Use `stabilize_media.py` for repairs now; full nested exports alone did not prove
durability on the user's BTT6.826 installation.

## Capture usage

Hold the modifier keys that show Media and click **Screen Cap**. Capture happens
immediately, without the normal screenshot-selection UI or a fixed delay.
`capture_btt_menu.zsh` saves PNGs in a private capture directory under
`~/dev/scripts/tmp/btt-screenshots/`, then copies the first screen PNG to the
clipboard. On multiple displays, additional screen files remain in that directory.
Release the menu keys afterward and paste the screenshot into chat if desired.
Nothing is uploaded or opened automatically.

BTT's Screen Recording permission was already granted when this was prepared.
If a capture fails later, the shell action reports an error; a successful file
capture is retained even if copying to the clipboard fails.

## Evidence and limitations

The live BTT6.826 database contained empty rear/Sonos submenu parents and Notes
buttons without actions. Their definitions were recoverable from earlier private
backups. Notes actions were already absent in the05:30label backup; rear/Sonos
descendants were already absent in the11:57sizing backup. Those timelines do not
establish which update/migration originally removed them.

Validation uses replacement-style mock API behavior, verifies backup failure
prevents edits, tests missing-action failures and idempotence, and compiles JXA
and the capture AppleScript. Agent GUI/Apple Events access is blocked, so actual
installation and screen capture must run in the user's Terminal/BTT session.
