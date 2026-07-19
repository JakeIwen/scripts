#!/bin/zsh

# ONE-OFF RECOVERY: quarantine the exact stale Media menu whose embedded
# identifier and modifier chord collided with the current Media menu.
# Both menu UUIDs and the BTT database build are fixed below.

set -euo pipefail

readonly db_path="$HOME/Library/Application Support/BetterTouchTool/btt_data_store.version_6_011_build_2026010801"
readonly current_uuid="D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289"
readonly duplicate_uuid="1DF14D5D-24D7-4950-84B1-86CF46BB3864"
readonly quarantine_identifier="Media-quarantined-1DF14D5D"

fail() {
  print -u2 -- "ERROR: $*"
  exit 1
}

[[ -f "$db_path" ]] || fail "BTT database not found: $db_path"
command -v sqlite3 >/dev/null 2>&1 || fail "sqlite3 is required"

if pgrep -x BetterTouchTool >/dev/null 2>&1; then
  fail "BetterTouchTool is running. Quit it with Command-Q, then rerun this command."
fi

sync_active="$(defaults read com.hegenberg.BetterTouchTool BTTDropboxSyncActive 2>/dev/null || print 0)"
[[ "$sync_active" == 0 ]] || fail "BTT cloud sync is enabled; refusing because it could restore the collision"

integrity="$(sqlite3 "$db_path" 'PRAGMA integrity_check;')"
[[ "$integrity" == ok ]] || fail "database integrity check failed: $integrity"

for uuid in "$current_uuid" "$duplicate_uuid"; do
  count="$(sqlite3 "$db_path" "SELECT COUNT(*) FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER='$uuid';")"
  [[ "$count" == 1 ]] || fail "expected exactly one record for $uuid"
done

current_identifier="$(sqlite3 "$db_path" "SELECT json_extract(CAST(substr(ZICONDATA3,2) AS TEXT),'$.BTTMenuElementIdentifier') FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER='$current_uuid';")"
duplicate_identifier="$(sqlite3 "$db_path" "SELECT json_extract(CAST(substr(ZICONDATA3,2) AS TEXT),'$.BTTMenuElementIdentifier') FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER='$duplicate_uuid';")"
[[ "$current_identifier" == Media && "$duplicate_identifier" == Media ]] || fail "the expected embedded Media collision is not present"

timestamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="${db_path:h}/manual-media-modifier-fix-backups"
mkdir -p "$backup_dir"
cp -p "$db_path" "$backup_dir/${db_path:t}.$timestamp"
for suffix in -wal -shm; do
  [[ -f "$db_path$suffix" ]] && cp -p "$db_path$suffix" "$backup_dir/${db_path:t}.$timestamp$suffix"
done

sqlite3 -bail "$db_path" <<SQL
PRAGMA busy_timeout=5000;
BEGIN IMMEDIATE;
UPDATE ZBTTBASEENTITY
SET ZICONDATA3 = CAST(
      char(1) || json_set(
        CAST(substr(ZICONDATA3,2) AS TEXT),
        '\$.BTTMenuElementIdentifier', '$quarantine_identifier',
        '\$.BTTMenuModifierKeys', 0,
        '\$.BTTMenuVisibility', 1
      )
      AS BLOB
    ),
    ZADDITIONALSTRING = 'Media (quarantined duplicate)',
    ZISENABLED = 0,
    ZENABLEDNEW = 0,
    Z_OPT = COALESCE(Z_OPT,0) + 1
WHERE ZUNIQUEIDENTIFIER = '$duplicate_uuid';
COMMIT;
SQL

integrity="$(sqlite3 "$db_path" 'PRAGMA integrity_check;')"
[[ "$integrity" == ok ]] || fail "database integrity check failed after update: $integrity"

state="$(sqlite3 "$db_path" "SELECT json_extract(CAST(substr(ZICONDATA3,2) AS TEXT),'$.BTTMenuElementIdentifier') || ',' || json_extract(CAST(substr(ZICONDATA3,2) AS TEXT),'$.BTTMenuModifierKeys') || ',' || ZISENABLED || ',' || ZENABLEDNEW FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER='$duplicate_uuid';")"
[[ "$state" == "$quarantine_identifier,0,0,0" ]] || fail "quarantine state did not persist: $state"

print -- "Removed the duplicate Media identifier/modifier collision."
print -- "No records were deleted. Reopen BetterTouchTool and test Control+Option+Command."
print -- "Automatic backup: $backup_dir/${db_path:t}.$timestamp"
