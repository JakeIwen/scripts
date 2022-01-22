#! /bin/bash

set -eu # exit on error

btt_bkdir="$HOME/backups/BetterTouchTool"
btt_app_path="$HOME/Library/Application Support/BetterTouchTool"
btt_emergency_path="$HOME/Library/Application Support/BetterTouchTool.bad"

btt_set_vars() {
  btt_oldest_backup_folder="$(ls -lt "$btt_bkdir" | tail -1 | grep -Eo "\w+$")"
  btt_oldest_backup_path="$btt_bkdir/$btt_oldest_backup_folder"
  btt_most_recent_backup_folder="$(ls -ltr "$btt_bkdir" | tail -1 | grep -Eo "\w+$")"
  btt_most_recent_backup_path="$btt_bkdir/$btt_most_recent_backup_folder"
}

btt_backup() { 
  echo "backing up to $btt_oldest_backup_path"
  rm -rf "$btt_oldest_backup_path"
  mkdir -p "$btt_oldest_backup_path"
  cp -r "$btt_app_path/" "$btt_oldest_backup_path/"
  echo "done. ls -lah of $btt_bkdir:"
  ls -lah "$btt_bkdir"
}

btt_restore() { 
  mkdir -p "$btt_emergency_path"
  echo "copying to $btt_emergency_path"
  cp -r "$btt_app_path" "$btt_emergency_path"
  echo "clearing ${btt_app_path:?}/*"
  rm -rf "${btt_app_path:?}/*"
  echo "copying $btt_most_recent_backup_path/*"
  cp -r "$btt_most_recent_backup_path/*" "$btt_app_path"
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

echo "checking/creating backup folders"
btt_init_backup_directories
btt_set_vars
btt_backup

