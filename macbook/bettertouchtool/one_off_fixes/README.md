# Historical BetterTouchTool repairs

The dated July and September 2026 repair executables were retired after their
migrations and recoveries completed. They targeted fixed database snapshots,
menu UUIDs and application versions. Their source remains in Git history.

BTT 6.011 could crash when importing the generated submenu JSON. Duplicate Media
identifiers and modifier chords also caused stale menus or missing shortcuts.
Recovery restored the known snapshot, repaired its duplicate/order state and
isolated the stale menu identifier. Those exact database edits are not a general
recovery procedure.
