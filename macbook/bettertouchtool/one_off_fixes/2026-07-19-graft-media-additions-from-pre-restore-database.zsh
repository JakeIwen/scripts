#!/bin/zsh

# ONE-OFF RECOVERY: graft the seven July 19 Media additions from the exact
# preserved pre-restore BTT database into the restored database lineage.
# Paths, database build, UUIDs, expected row count, and final order are fixed.

set -euo pipefail

readonly source_dir="$HOME/Library/Application Support/BetterTouchTool.pre-yesterday-restore-20260719-082154"
readonly live_dir="$HOME/Library/Application Support/BetterTouchTool"
readonly db_name="btt_data_store.version_6_011_build_2026010801"
readonly source_db="$source_dir/$db_name"
readonly target_db="${1:-$live_dir/$db_name}"
readonly media_uuid="D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289"
readonly -a addition_uuids=(
  "3C7CCA4E-B883-47D5-8A9D-143BD4C2B6F2" # Emoji
  "75C1EC53-E8CA-4583-B4C2-108564BB18C3" # addr
  "BECB8012-2512-4234-9C48-CF12F609FEF2" # Sidecar
  "1FD3C982-6A43-415A-BD06-F6E7C253A859" # Spktv
  "36C78376-090D-4A94-8608-61D83F4E6FC5" # Night Shift
  "3A0CD200-5565-4C8A-A766-7E3ED9EF95F4" # Display Power
  "0F8AEB59-60C1-4B1F-A356-26AD45E3A0A3" # Recent Notes
)

fail() {
  print -u2 -- "ERROR: $*"
  exit 1
}

[[ -f "$source_db" ]] || fail "preserved source database is missing: $source_db"
[[ -f "$target_db" ]] || fail "target database is missing: $target_db"
command -v sqlite3 >/dev/null 2>&1 || fail "sqlite3 is required"

if [[ "$target_db" == "$live_dir/$db_name" ]] && pgrep -x BetterTouchTool >/dev/null 2>&1; then
  fail "BetterTouchTool is running. Quit it with Command-Q, then rerun this command."
fi

if [[ "$target_db" == "$live_dir/$db_name" ]]; then
  sync_active="$(defaults read com.hegenberg.BetterTouchTool BTTDropboxSyncActive 2>/dev/null || print 0)"
  [[ "$sync_active" == 0 ]] || fail "BTT cloud sync is enabled; refusing because it would undo this repair"
fi

for db in "$source_db" "$target_db"; do
  integrity="$(sqlite3 "$db" 'PRAGMA integrity_check;')"
  [[ "$integrity" == ok ]] || fail "integrity check failed for $db: $integrity"
done

media_count="$(sqlite3 "$target_db" "SELECT COUNT(*) FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER='$media_uuid';")"
[[ "$media_count" == 1 ]] || fail "expected exactly one current Media menu in target"

uuid_sql="$(printf "'%s'," "${addition_uuids[@]}")"
uuid_sql="${uuid_sql%,}"

source_root_count="$(sqlite3 "$source_db" "SELECT COUNT(*) FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER IN ($uuid_sql);")"
[[ "$source_root_count" == 7 ]] || fail "source does not contain all seven additions"

target_root_count="$(sqlite3 "$target_db" "SELECT COUNT(*) FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER IN ($uuid_sql);")"
[[ "$target_root_count" == 0 ]] || fail "target already contains one or more additions; refusing to duplicate them"

timestamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="${target_db:h}/manual-media-graft-backups"
mkdir -p "$backup_dir"
cp -p "$target_db" "$backup_dir/${target_db:t}.$timestamp"
for suffix in -wal -shm; do
  [[ -f "$target_db$suffix" ]] && cp -p "$target_db$suffix" "$backup_dir/${target_db:t}.$timestamp$suffix"
done

sqlite3 -bail "$target_db" <<SQL
PRAGMA busy_timeout=5000;
ATTACH DATABASE '$source_db' AS preserved;
BEGIN IMMEDIATE;

CREATE TEMP TABLE graft_pks (pk INTEGER PRIMARY KEY);
INSERT INTO graft_pks
WITH RECURSIVE
roots(pk) AS (
  SELECT Z_PK FROM preserved.ZBTTBASEENTITY
  WHERE ZUNIQUEIDENTIFIER IN ($uuid_sql)
),
tree(pk) AS (
  SELECT pk FROM roots
  UNION ALL
  SELECT child.Z_PK
  FROM preserved.ZBTTBASEENTITY AS child
  JOIN tree ON child.ZPARENT = tree.pk
)
SELECT pk FROM tree;

-- These snapshots share the exact same Core Data schema and database lineage.
-- Copy each complete menu/action row plus its Global-app membership.
INSERT INTO main.ZBTTBASEENTITY
SELECT source_row.*
FROM preserved.ZBTTBASEENTITY AS source_row
JOIN graft_pks ON graft_pks.pk = source_row.Z_PK;

INSERT INTO main.Z_2APPS_GESTURES
SELECT membership.*
FROM preserved.Z_2APPS_GESTURES AS membership
JOIN graft_pks ON graft_pks.pk = membership.Z_9APPS_GESTURES;

-- Attach the seven roots to the clean Media menu in a deterministic order.
UPDATE main.ZBTTBASEENTITY
SET ZPARENT = (SELECT Z_PK FROM main.ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER='$media_uuid'),
    ZORDER = CASE ZUNIQUEIDENTIFIER
      WHEN '3C7CCA4E-B883-47D5-8A9D-143BD4C2B6F2' THEN 1000
      WHEN '75C1EC53-E8CA-4583-B4C2-108564BB18C3' THEN 1001
      WHEN 'BECB8012-2512-4234-9C48-CF12F609FEF2' THEN 1002
      WHEN '1FD3C982-6A43-415A-BD06-F6E7C253A859' THEN 1003
      WHEN '36C78376-090D-4A94-8608-61D83F4E6FC5' THEN 1004
      WHEN '3A0CD200-5565-4C8A-A766-7E3ED9EF95F4' THEN 1005
      WHEN '0F8AEB59-60C1-4B1F-A356-26AD45E3A0A3' THEN 1006
    END,
    ZISENABLED = 1,
    ZENABLEDNEW = 1,
    Z_OPT = COALESCE(Z_OPT, 0) + 1
WHERE ZUNIQUEIDENTIFIER IN ($uuid_sql);

CREATE TEMP TABLE media_order_map AS
SELECT Z_PK, ROW_NUMBER() OVER (ORDER BY ZORDER, Z_PK) - 1 AS new_order
FROM main.ZBTTBASEENTITY
WHERE ZPARENT = (SELECT Z_PK FROM main.ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER='$media_uuid');

UPDATE main.ZBTTBASEENTITY
SET ZORDER = (SELECT new_order FROM media_order_map WHERE media_order_map.Z_PK=ZBTTBASEENTITY.Z_PK),
    Z_OPT = COALESCE(Z_OPT, 0) + 1
WHERE Z_PK IN (SELECT Z_PK FROM media_order_map);

UPDATE main.Z_PRIMARYKEY
SET Z_MAX = (SELECT MAX(Z_PK) FROM main.ZBTTBASEENTITY)
WHERE Z_ENT = 1;

DROP TABLE media_order_map;
DROP TABLE graft_pks;
COMMIT;
DETACH DATABASE preserved;
SQL

integrity="$(sqlite3 "$target_db" 'PRAGMA integrity_check;')"
[[ "$integrity" == ok ]] || fail "target integrity check failed after graft: $integrity"

target_root_count="$(sqlite3 "$target_db" "SELECT COUNT(*) FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER IN ($uuid_sql);")"
[[ "$target_root_count" == 7 ]] || fail "not all addition roots were copied"

media_pk="$(sqlite3 "$target_db" "SELECT Z_PK FROM ZBTTBASEENTITY WHERE ZUNIQUEIDENTIFIER='$media_uuid';")"
order_state="$(sqlite3 "$target_db" "SELECT printf('%s,%s,%s,%s',COUNT(*),MIN(ZORDER),MAX(ZORDER),COUNT(*)-COUNT(DISTINCT ZORDER)) FROM ZBTTBASEENTITY WHERE ZPARENT=$media_pk;")"
[[ "$order_state" == "25,0,24,0" ]] || fail "unexpected Media order state after graft: $order_state"

print -- "Media additions graft completed successfully."
print -- "Copied 86 complete records; Media now has 25 items with orders 0-24."
print -- "Automatic pre-graft database copy: $backup_dir/${target_db:t}.$timestamp"
