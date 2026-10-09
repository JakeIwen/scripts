# Historical BetterTouchTool repairs

The dated July and September 2026 repair executables were retired after their
migrations and recoveries completed. They targeted fixed database snapshots,
menu UUIDs and application versions. Their source remains in Git history.

BTT 6.011 could crash when importing the generated submenu JSON. Duplicate Media
identifiers and modifier chords also caused stale menus or missing shortcuts.
Recovery restored the known snapshot, repaired its duplicate/order state and
isolated the stale menu identifier. Those exact database edits are not a general
recovery procedure.

The later nested-import recovery appeared to work in memory but lost descendants
after restart. Current recovery creates individual records and verifies saved
state after restart. BTT 6.826 also lost held-modifier clicks when
`BTTMenuDisableDrag` was enabled; the maintained stabilizer preserves that setting.

Use [the maintained BTT CLI](../README.md) for inspection, backups and supported
repairs. Reusable recovery planning remains in `../lib/media_recovery.js`.
