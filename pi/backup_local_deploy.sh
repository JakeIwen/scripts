#!/bin/bash
# Shared payload and drift checks for the two targeted cloud installers.
LOCAL_BACKUP_FILES=(backup_lock_io.sh backup_locks.sh backup_lifecycle.sh umount_disks_fence.sh backup_window.sh pi_backup.sh exfat_snapshot.sh openwrt_backup.sh abort_backup.sh clone_now.sh)
# Cloud worker modules both installers publish.
CLOUD_WORKER_FILES=(time_machine_icloud.py time_machine_icloud.sh time_machine_pipeline.py time_machine_staging.py
  time_machine_store.py time_machine_verification.py time_machine_downloads.py time_machine_icloud_control.py
  time_machine_icloud_status.py icloud_backup.sh icloud_progress.py icloud_inventory.py icloud_uplink.py
  icloud_backup_control.py mac_capture_control.py backup_priority_control.py)
LOCAL_BACKUP_CHECKED=(backup_conf.sh backup_priority.py cloud_backup_control.py protected_json.py backup_locks.py
  "${LOCAL_BACKUP_FILES[@]}" "${CLOUD_WORKER_FILES[@]}")
# Drift-checked against the installers' own reviewed hashes
# (VANPI_TM_PREVIOUS_CLOUD_SHA256, VANPI_TM_PREVIOUS_STATUS_SHA256) because a
# second, still-uncommitted cutover may need a hash that is not in Git yet.
LOCAL_BACKUP_OVERRIDDEN=(icloud_backup.py icloud_status.py)
# Everything both installers stage and publish into /home/pi/scripts/backup.
LOCAL_BACKUP_PAYLOAD=("${LOCAL_BACKUP_CHECKED[@]}" "${LOCAL_BACKUP_OVERRIDDEN[@]}"
  ICLOUD_RESTORE.txt TIME_MACHINE_ICLOUD_RESTORE.txt)
# Disk lifecycle callers of abort_backup.sh live outside the backup directory.
LIFECYCLE_SCRIPTS=(umount_disks.sh internet_switches.sh)
LOCAL_BACKUP_SUDO=${LOCAL_BACKUP_SUDO-/usr/bin/sudo -n}
LOCAL_BACKUP_LIFECYCLE_OWNER=${LOCAL_BACKUP_LIFECYCLE_OWNER:-pi:pi}
LOCAL_BACKUP_STAT=${LOCAL_BACKUP_STAT:-/usr/bin/stat}

local_backup_expected_hash() { # <name:hash,...> <name>: the recorded hash, empty when unknown
  printf '%s' "$1" | /usr/bin/tr ',' '\n' | /usr/bin/awk -F: -v name="$2" '$1 == name {print $2}'
}

local_backup_previous_conf_hash() {
  local repo=$1 revision=${VANPI_BACKUP_PREVIOUS_REVISION:-HEAD} hash
  hash=${VANPI_BACKUP_PREVIOUS_CONF_SHA256:-$(git -C "$repo" show "$revision:pi/scripts/backup/backup_conf.sh" | shasum -a 256)}
  hash=${hash%% *}
  [[ "$hash" =~ ^[0-9a-f]{64}$ ]] || return 1
  printf '%s\n' "$hash"
}

local_backup_previous_tmpfiles_hash() {
  local repo=$1 revision=${VANPI_BACKUP_PREVIOUS_REVISION:-HEAD} hash
  hash=$(git -C "$repo" show "$revision:pi/tmpfiles.d/vanpi-backup.conf" | shasum -a 256) || return 1
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

verify_local_backup_tmpfiles() {
  local source=$1 previous=$2 live=${3:-/etc/tmpfiles.d/vanpi-backup.conf} actual staged
  [[ "$previous" =~ ^[0-9a-f]{64}$ ]] || return 1
  [[ -f "$source" && ! -L "$source" ]] || return 1
  if [[ ! -e "$live" && ! -L "$live" ]]; then
    return 0
  fi
  if [[ ! -f "$live" || -L "$live" ]]; then
    echo 'Live tmpfiles registry differs; inspect before deploying.' >&2
    return 1
  fi
  actual=$(sha256sum "$live"); actual=${actual%% *}
  staged=$(sha256sum "$source"); staged=${staged%% *}
  if [[ "$actual" != "$previous" && "$actual" != "$staged" ]]; then
    echo 'Live tmpfiles registry differs; inspect before deploying.' >&2
    return 1
  fi
}

verify_local_backup_payload() {
  local source_dir=$1 previous=$2 live_dir=${3:-/home/pi/scripts/backup} name expected actual staged
  for name in "${LOCAL_BACKUP_CHECKED[@]}"; do
    expected=$(local_backup_expected_hash "$previous" "$name")
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

local_backup_previous_script_hash() { # <repo> <path-under-pi/scripts>
  local repo=$1 name=$2 revision=${VANPI_BACKUP_PREVIOUS_REVISION:-HEAD} hash
  hash=$(git -C "$repo" show "$revision:pi/scripts/$name" | shasum -a 256) || return 1
  hash=${hash%% *}
  [[ "$hash" =~ ^[0-9a-f]{64}$ ]] || return 1
  printf '%s\n' "$hash"
}

local_backup_previous_lifecycle_hashes() { # <repo> -> name:hash, for every lifecycle script
  local repo=$1 name hash
  for name in "${LIFECYCLE_SCRIPTS[@]}"; do
    hash=$(local_backup_previous_script_hash "$repo" "$name") || return 1
    printf '%s:%s,' "$name" "$hash"
  done
}

verify_lifecycle_script() { # <staged> <live> <previous-hash>: read-only; prints "owner group mode"
  local staged=$1 live=$2 previous=$3 actual expected owner group mode extra
  [[ "$previous" =~ ^[0-9a-f]{64}$ && -f "$staged" && ! -L "$staged" ]] || return 1
  [[ -f "$live" && ! -L "$live" ]] || { echo "Disk lifecycle script is missing or unsafe: $live" >&2; return 1; }
  actual=$(sha256sum "$live"); actual=${actual%% *}
  expected=$(sha256sum "$staged"); expected=${expected%% *}
  [[ "$actual" == "$previous" || "$actual" == "$expected" ]] ||
    { echo "Live code differs: $live; inspect before deploying." >&2; return 1; }
  read -r owner group mode extra <<< "$("$LOCAL_BACKUP_STAT" -c '%U %G %a' "$live")"
  [[ "$owner:$group" == "$LOCAL_BACKUP_LIFECYCLE_OWNER" && "$mode" =~ ^[0-7]{3,4}$ && -z ${extra:-} ]] ||
    { echo "Disk lifecycle script ownership differs: $live; inspect before deploying." >&2; return 1; }
  printf '%s %s %s\n' "$owner" "$group" "$mode"
}

verify_lifecycle_scripts() { # <staged-dir> <name:hash,...> [live-dir]: preflight, before any live mutation
  local staged_dir=$1 previous=$2 live_dir=${3:-/home/pi/scripts} name
  for name in "${LIFECYCLE_SCRIPTS[@]}"; do
    verify_lifecycle_script "$staged_dir/$name" "$live_dir/$name" \
      "$(local_backup_expected_hash "$previous" "$name")" >/dev/null || return 1
  done
}

install_lifecycle_script() { # <staged> <live> <previous-hash> <previous-dir>
  # Drift-checked, atomic replacement that preserves the live owner and mode;
  # both installers use it so a host never runs new jobs with an old caller.
  local staged=$1 live=$2 previous=$3 previous_dir=$4 metadata owner group mode
  metadata=$(verify_lifecycle_script "$staged" "$live" "$previous") || return 1
  read -r owner group mode <<< "$metadata"
  /usr/bin/cp -p "$live" "$previous_dir/${live##*/}" || return 1
  $LOCAL_BACKUP_SUDO /usr/bin/install -o "$owner" -g "$group" -m "$mode" "$staged" "$live.new" || return 1
  $LOCAL_BACKUP_SUDO /usr/bin/mv -Tf "$live.new" "$live"
}

install_lifecycle_scripts() { # <staged-dir> <name:hash,...> <previous-dir> [live-dir]
  local staged_dir=$1 previous=$2 previous_dir=$3 live_dir=${4:-/home/pi/scripts} name
  for name in "${LIFECYCLE_SCRIPTS[@]}"; do
    install_lifecycle_script "$staged_dir/$name" "$live_dir/$name" \
      "$(local_backup_expected_hash "$previous" "$name")" "$previous_dir" || return 1
  done
}
