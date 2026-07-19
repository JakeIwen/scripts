#!/bin/zsh

# ONE-OFF RECOVERY: detach the known duplicate Media record, optionally restore
# the known Display Power record, and compact the known Media item's ordering.
# The database build and all affected UUIDs are fixed below.

set -euo pipefail

readonly media_uuid="D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289"
readonly duplicate_uuid="1DF14D5D-24D7-4950-84B1-86CF46BB3864"
readonly display_power_uuid="3A0CD200-5565-4C8A-A766-7E3ED9EF95F4"
readonly live_db="$HOME/Library/Application Support/BetterTouchTool/btt_data_store.version_6_011_build_2026010801"
readonly db_path="${1:-$live_db}"

fail() {
  print -u2 -- "ERROR: $*"
  exit 1
}

[[ -f "$db_path" ]] || fail "BTT database not found: $db_path"
command -v sqlite3 >/dev/null 2>&1 || fail "sqlite3 is required"

# Editing Core Data while BTT has the database open risks losing or corrupting
# changes. Only enforce this for the actual live database; a copied database is
# used by this script's automated validation.
if [[ "$db_path" == "$live_db" ]] && pgrep -x BetterTouchTool >/dev/null 2>&1; then
  fail "BetterTouchTool is running. Quit it with Command-Q, then rerun this command."
fi

integrity="$(sqlite3 "$db_path" 'PRAGMA integrity_check;')"
[[ "$integrity" == "ok" ]] || fail "database integrity check failed before repair: $integrity"

row_count() {
  sqlite3 "$db_path" "SELECT COUNT(*) FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER='$1';"
}

[[ "$(row_count "$media_uuid")" == 1 ]] || fail "expected exactly one current Media record"
[[ "$(row_count "$duplicate_uuid")" == 1 ]] || fail "expected exactly one duplicate Media record"
display_power_count="$(row_count "$display_power_uuid")"
[[ "$display_power_count" == 0 || "$display_power_count" == 1 ]] || fail "expected zero or one Display Power record"

readonly media_pk="$(sqlite3 "$db_path" "SELECT Z_PK FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER='$media_uuid';")"
readonly duplicate_pk="$(sqlite3 "$db_path" "SELECT Z_PK FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER='$duplicate_uuid';")"
readonly duplicate_parent="$(sqlite3 "$db_path" "SELECT COALESCE(ZPARENT, -1) FROM ZBTTBASEENTITY WHERE Z_PK=$duplicate_pk;")"

[[ "$duplicate_parent" == "$media_pk" || "$duplicate_parent" == -1 ]] || fail "duplicate Media has an unexpected parent; refusing an unverified repair"

backup_dir="${db_path:h}/manual-media-repair-backups"
timestamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$backup_dir"
cp -p "$db_path" "$backup_dir/${db_path:t}.$timestamp"
for suffix in -wal -shm; do
  [[ -f "$db_path$suffix" ]] && cp -p "$db_path$suffix" "$backup_dir/${db_path:t}.$timestamp$suffix"
done

sqlite3 -bail "$db_path" <<SQL
PRAGMA busy_timeout=5000;
BEGIN IMMEDIATE;

-- Preserve the old menu, but make it a disabled, clearly named top-level
-- record instead of an invalid full floating menu nested among menu items.
UPDATE ZBTTBASEENTITY
SET ZPARENT = NULL,
    ZORDER = 0,
    ZISENABLED = 0,
    ZENABLEDNEW = 0,
    ZADDITIONALSTRING = 'Media (quarantined duplicate)',
    Z_OPT = COALESCE(Z_OPT, 0) + 1
WHERE Z_PK = $duplicate_pk;

-- If this valid item exists, it was detached by the failed imports. Restore it
-- at the end. It is absent in the pre-July-19 snapshot and will be imported
-- later as part of the clean additions bundle.
UPDATE ZBTTBASEENTITY
SET ZPARENT = $media_pk,
    ZORDER = 1000000,
    ZISENABLED = 1,
    ZENABLEDNEW = 1,
    Z_OPT = COALESCE(Z_OPT, 0) + 1
WHERE ZUNIQUEIDENTIFIER = '$display_power_uuid';

-- BTT indexes this array directly. Gaps such as 0,2,...,24 caused its
-- __boundsFail exception, so compact the surviving children to 0...(n-1).
CREATE TEMP TABLE media_order_map AS
SELECT Z_PK,
       ROW_NUMBER() OVER (ORDER BY ZORDER, Z_PK) - 1 AS new_order
FROM ZBTTBASEENTITY
WHERE ZPARENT = $media_pk;

UPDATE ZBTTBASEENTITY
SET ZORDER = (SELECT new_order FROM media_order_map WHERE media_order_map.Z_PK = ZBTTBASEENTITY.Z_PK),
    Z_OPT = COALESCE(Z_OPT, 0) + 1
WHERE Z_PK IN (SELECT Z_PK FROM media_order_map);

DROP TABLE media_order_map;
COMMIT;
SQL

integrity="$(sqlite3 "$db_path" 'PRAGMA integrity_check;')"
[[ "$integrity" == "ok" ]] || fail "database integrity check failed after repair: $integrity"

duplicate_state="$(sqlite3 "$db_path" "SELECT printf('%s,%s,%s', COALESCE(ZPARENT,'NULL'),ZISENABLED,ZENABLEDNEW) FROM ZBTTBASEENTITY WHERE Z_PK=$duplicate_pk;")"
[[ "$duplicate_state" == "NULL,0,0" ]] || fail "duplicate quarantine did not persist"

order_state="$(sqlite3 "$db_path" "SELECT printf('%s,%s,%s',COUNT(*),MIN(ZORDER),MAX(ZORDER)) FROM ZBTTBASEENTITY WHERE ZPARENT=$media_pk;")"
order_count="${order_state%%,*}"
[[ "$order_state" == "$order_count,0,$((order_count - 1))" ]] || fail "Media item orders are not contiguous after repair: $order_state"

duplicate_orders="$(sqlite3 "$db_path" "SELECT COUNT(*)-COUNT(DISTINCT ZORDER) FROM ZBTTBASEENTITY WHERE ZPARENT=$media_pk;")"
[[ "$duplicate_orders" == 0 ]] || fail "Media still has duplicate item orders"

if [[ "$display_power_count" == 1 ]]; then
  display_parent="$(sqlite3 "$db_path" "SELECT COALESCE(ZPARENT,-1) FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER='$display_power_uuid';")"
  [[ "$display_parent" == "$media_pk" ]] || fail "Display Power was not restored"
fi

print -- "Media repair completed successfully."
print -- "Database: $db_path"
print -- "Automatic pre-repair copy: $backup_dir/${db_path:t}.$timestamp"
print -- "Current Media items: $order_count (orders 0-$((order_count - 1)))"
print -- "Old Media: detached, disabled, and renamed; no records were deleted."
