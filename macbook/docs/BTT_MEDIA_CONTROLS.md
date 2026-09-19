# Media controls recovery and diagnostic screenshot

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
