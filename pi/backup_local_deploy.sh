#!/bin/bash
# Shared payload and drift checks for the two targeted cloud installers.
LOCAL_FAIRNESS_FILES=(local_fairness.py local_fairness_window.py local_fairness_rollback.py backup_window.sh pi_backup.sh exfat_snapshot.sh openwrt_backup.sh)
LOCAL_BACKUP_CHECKED=(backup_conf.sh backup_priority.py cloud_backup_control.py protected_json.py "${LOCAL_FAIRNESS_FILES[@]}")

local_backup_previous_conf_hash() {
  local repo=$1 revision=${VANPI_BACKUP_PREVIOUS_REVISION:-HEAD} hash
  hash=${VANPI_BACKUP_PREVIOUS_CONF_SHA256:-$(git -C "$repo" show "$revision:pi/scripts/backup/backup_conf.sh" | shasum -a 256)}
  hash=${hash%% *}
  [[ "$hash" =~ ^[0-9a-f]{64}$ ]] || return 1
  printf '%s\n' "$hash"
}

local_backup_previous_hashes() {
  local repo=$1 name hash entry revision=${VANPI_BACKUP_PREVIOUS_REVISION:-HEAD}
  git -C "$repo" rev-parse --verify "$revision^{commit}" >/dev/null || return 1
  for name in "${LOCAL_BACKUP_CHECKED[@]}"; do
    if [[ "$name" == backup_conf.sh ]]; then
      hash=$(local_backup_previous_conf_hash "$repo") || return 1
    else
      entry=$(git -C "$repo" ls-tree "$revision" -- "pi/scripts/backup/$name") || return 1
      if [[ -z "$entry" ]]; then
        hash=absent
      else
        hash=$(git -C "$repo" show "$revision:pi/scripts/backup/$name" | shasum -a 256) || return 1
      fi
    fi
    printf '%s:%s,' "$name" "${hash%% *}"
  done
}

verify_local_backup_payload() {
  local source_dir=$1 previous=$2 live_dir=${3:-/home/pi/scripts/backup} name expected actual staged
  for name in "${LOCAL_BACKUP_CHECKED[@]}"; do
    expected=$(printf '%s' "$previous" | /usr/bin/tr ',' '\n' | /usr/bin/awk -F: -v name="$name" '$1 == name {print $2}')
    [[ "$expected" == absent || "$expected" =~ ^[0-9a-f]{64}$ ]] || return 1
    [[ -f "$source_dir/$name" && ! -L "$source_dir/$name" ]] || return 1
    if [[ "$expected" == absent && ! -e "$live_dir/$name" && ! -L "$live_dir/$name" ]]; then
      continue
    fi
    [[ -f "$live_dir/$name" && ! -L "$live_dir/$name" ]] || return 1
    actual=$(sha256sum "$live_dir/$name"); actual=${actual%% *}
    staged=$(sha256sum "$source_dir/$name"); staged=${staged%% *}
    if [[ "$actual" != "$expected" && "$actual" != "$staged" ]]; then
      printf 'Live backup source differs: %s; inspect before deploying.\n' "$name" >&2
      return 1
    fi
  done
}
