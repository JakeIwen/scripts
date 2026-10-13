#!/bin/bash
# Shared payload and drift checks for the two targeted cloud installers.
LOCAL_FAIRNESS_FILES=(local_fairness.py local_fairness_window.py local_fairness_rollback.py backup_window.sh pi_backup.sh exfat_snapshot.sh openwrt_backup.sh)
LOCAL_BACKUP_EXISTING=(backup_conf.sh backup_priority.py cloud_backup_control.py backup_window.sh pi_backup.sh exfat_snapshot.sh openwrt_backup.sh)

local_backup_previous_conf_hash() {
  local repo=$1 revision=${VANPI_BACKUP_PREVIOUS_REVISION:-HEAD} hash
  hash=${VANPI_BACKUP_PREVIOUS_CONF_SHA256:-$(git -C "$repo" show "$revision:pi/scripts/backup/backup_conf.sh" | shasum -a 256)}
  hash=${hash%% *}
  [[ "$hash" =~ ^[0-9a-f]{64}$ ]] || return 1
  printf '%s\n' "$hash"
}

local_backup_previous_hashes() {
  local repo=$1 name hash revision=${VANPI_BACKUP_PREVIOUS_REVISION:-HEAD}
  git -C "$repo" rev-parse --verify "$revision^{commit}" >/dev/null
  for name in "${LOCAL_BACKUP_EXISTING[@]}"; do
    if [[ "$name" == backup_conf.sh ]]; then
      hash=$(local_backup_previous_conf_hash "$repo") || return 1
    else
      hash=$(git -C "$repo" show "$revision:pi/scripts/backup/$name" | shasum -a 256)
    fi
    printf '%s:%s,' "$name" "${hash%% *}"
  done
}

verify_local_backup_payload() {
  local source_dir=$1 previous=$2 name expected actual staged
  for name in "${LOCAL_BACKUP_EXISTING[@]}"; do
    expected=$(printf '%s' "$previous" | /usr/bin/tr ',' '\n' | /usr/bin/awk -F: -v name="$name" '$1 == name {print $2}')
    [[ "$expected" =~ ^[0-9a-f]{64}$ ]] || return 1
    [[ -f /home/pi/scripts/backup/$name && ! -L /home/pi/scripts/backup/$name ]] || return 1
    actual=$(sha256sum "/home/pi/scripts/backup/$name"); actual=${actual%% *}
    staged=$(sha256sum "$source_dir/$name"); staged=${staged%% *}
    if [[ "$actual" != "$expected" && "$actual" != "$staged" ]]; then
      printf 'Live backup source differs: %s; inspect before deploying.\n' "$name" >&2
      return 1
    fi
  done
}
