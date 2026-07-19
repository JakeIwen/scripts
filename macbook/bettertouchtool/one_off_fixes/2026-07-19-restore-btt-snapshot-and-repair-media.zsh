#!/bin/zsh

# ONE-OFF RECOVERY: restore Jacob's exact July 18 BTT snapshot through a staged
# copy, run the paired Media repair, and preserve the displaced live directory.

set -euo pipefail

readonly source_dir="$HOME/backups/BetterTouchTool/reorg/btt1"
readonly support_parent="$HOME/Library/Application Support"
readonly live_dir="$support_parent/BetterTouchTool"
readonly db_name="btt_data_store.version_6_011_build_2026010801"
readonly script_dir="${0:A:h}"

fail() {
  print -u2 -- "ERROR: $*"
  exit 1
}

[[ -d "$source_dir" ]] || fail "yesterday's BTT snapshot is missing: $source_dir"
[[ -f "$source_dir/$db_name" ]] || fail "snapshot database is missing"
[[ -d "$live_dir" ]] || fail "live BTT support directory is missing"
command -v sqlite3 >/dev/null 2>&1 || fail "sqlite3 is required"

if pgrep -x BetterTouchTool >/dev/null 2>&1; then
  fail "BetterTouchTool is running. Quit it with Command-Q, then rerun this command."
fi

sync_active="$(defaults read com.hegenberg.BetterTouchTool BTTDropboxSyncActive 2>/dev/null || print 0)"
[[ "$sync_active" == 0 ]] || fail "BTT cloud sync is still enabled. Run: defaults write com.hegenberg.BetterTouchTool BTTDropboxSyncActive -bool false"

integrity="$(sqlite3 "$source_dir/$db_name" 'PRAGMA integrity_check;')"
[[ "$integrity" == ok ]] || fail "snapshot integrity check failed: $integrity"

timestamp="$(date +%Y%m%d-%H%M%S)"
staging_dir="$support_parent/BetterTouchTool.restore-staging-$timestamp"
safety_dir="$support_parent/BetterTouchTool.pre-yesterday-restore-$timestamp"

[[ ! -e "$staging_dir" && ! -e "$safety_dir" ]] || fail "timestamped restore paths already exist"

mkdir "$staging_dir"
cp -a "$source_dir/." "$staging_dir/"

# Repair the known duplicate and the old order gap in the staged copy, before
# the restored data ever becomes live.
"$script_dir/2026-07-19-repair-media-duplicate-and-order-gaps.zsh" "$staging_dir/$db_name"

integrity="$(sqlite3 "$staging_dir/$db_name" 'PRAGMA integrity_check;')"
[[ "$integrity" == ok ]] || fail "staged database integrity check failed: $integrity"

# Both renames occur on the same filesystem. The current installation remains
# intact at safety_dir and can be moved back if needed.
mv "$live_dir" "$safety_dir"
mv "$staging_dir" "$live_dir"

print -- "Yesterday's BTT snapshot is restored and its Media duplicate is disabled."
print -- "Previous live installation preserved at: $safety_dir"
print -- "Cloud sync remains disabled. You can now reopen BetterTouchTool."
