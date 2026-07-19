#!/bin/zsh

set -eu
frequency="${1:-regular}"

btt_bkdir="$HOME/backups/BetterTouchTool/$frequency"
btt_app_path="$HOME/Library/Application Support/BetterTouchTool"
btt_emergency_path="$HOME/Library/Application Support/BetterTouchTool.bad"

btt_set_vars() {
  local -a slots
  slots=("$btt_bkdir"/btt<1-9>)
  btt_oldest_backup_path="$(ls -td -- $slots | tail -1)"
  btt_most_recent_backup_path="$(ls -td -- $slots | head -1)"
}

btt_backup() {
  echo "backing up to $btt_oldest_backup_path"
  case "$btt_oldest_backup_path" in
    "$btt_bkdir"/btt<1-9>) ;;
    *) echo "refusing unsafe BetterTouchTool backup path: $btt_oldest_backup_path" >&2; return 1 ;;
  esac
  rm -rf -- "$btt_oldest_backup_path"
  mkdir -p "$btt_oldest_backup_path"
  cp -a "$btt_app_path/." "$btt_oldest_backup_path/"
  echo "done. ls -lah of $btt_bkdir:"
  ls -lah "$btt_bkdir"
}

btt_restore() {
  mkdir -p "$btt_emergency_path"
  echo "copying to $btt_emergency_path"
  cp -a "$btt_app_path/." "$btt_emergency_path/"
  echo "clearing ${btt_app_path:?}/*"
  find "$btt_app_path" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  echo "copying $btt_most_recent_backup_path/."
  cp -a "$btt_most_recent_backup_path/." "$btt_app_path/"
  echo "done"
}

btt_init_backup_directories() {
  mkdir -p "$btt_bkdir/btt1"
  mkdir -p "$btt_bkdir/btt2"
  mkdir -p "$btt_bkdir/btt3"
  mkdir -p "$btt_bkdir/btt4"
  mkdir -p "$btt_bkdir/btt5"
  mkdir -p "$btt_bkdir/btt6"
  mkdir -p "$btt_bkdir/btt7"
  mkdir -p "$btt_bkdir/btt8"
  mkdir -p "$btt_bkdir/btt9"
}

[[ -d "$btt_app_path" ]] || { echo "BetterTouchTool data not found: $btt_app_path" >&2; exit 1; }
echo "checking/creating backup folders"
btt_init_backup_directories
btt_set_vars
# btt_restore
btt_backup
