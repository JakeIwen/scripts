#!/bin/bash
# File transaction behind backup_local_deploy.sh. The fixed plan is the only
# destination allowlist; no lock inode or runtime backup data is ever replaced.
LOCAL_BACKUP_SUDO=${LOCAL_BACKUP_SUDO-/usr/bin/sudo -n}
LOCAL_BACKUP_STAT=${LOCAL_BACKUP_STAT:-/usr/bin/stat}
LOCAL_BACKUP_SYSTEMCTL=${LOCAL_BACKUP_SYSTEMCTL:-/usr/bin/systemctl}
LOCAL_BACKUP_ROOT=${LOCAL_BACKUP_ROOT:-}
LOCAL_BACKUP_PI_OWNER=${LOCAL_BACKUP_PI_OWNER:-pi:pi}
LOCAL_BACKUP_ROOT_OWNER=${LOCAL_BACKUP_ROOT_OWNER:-root:root}
LOCAL_BACKUP_TIMERS=(vanpi-icloud-backup.timer vanpi-time-machine-icloud.timer
  vanpi-icloud-backup-resume.timer vanpi-time-machine-icloud-resume.timer)

local_backup_hash() {
  local digest
  digest=$($LOCAL_BACKUP_SUDO /usr/bin/sha256sum -- "$1") || return 1
  printf '%s\n' "${digest%% *}"
}

local_backup_metadata() {
  $LOCAL_BACKUP_SUDO "$LOCAL_BACKUP_STAT" -c '%U:%G|%a' -- "$1"
}

local_backup_snapshot_matches() { # <path> <sha256|absent> <owner:group> <mode>
  local path=$1 expected=$2 owner=$3 mode=$4 actual metadata
  if [[ "$expected" == absent ]]; then
    # An argument-less return during EXIT cleanup can reuse the trapped exit
    # status. Report the absence check explicitly, including after removal.
    [[ ! -e "$path" && ! -L "$path" ]] || return 1
    return 0
  fi
  [[ -f "$path" && ! -L "$path" ]] || return 1
  actual=$(local_backup_hash "$path") || return 1
  metadata=$(local_backup_metadata "$path") || return 1
  [[ "$actual" == "$expected" && "$metadata" == "$owner|$mode" ]]
}

local_backup_check_record() { # <source> <live> <policy> <owner> <mode> <first> <previous>
  local source=$1 live=$2 policy=$3 owner=$4 mode=$5 first=$6 expected=$7
  local actual=absent old_owner=- old_mode=- staged metadata
  [[ -f "$source" && ! -L "$source" ]] || return 1
  [[ -d "${live%/*}" && ! -L "${live%/*}" ]] || return 1
  case "$owner" in pi:pi) owner=$LOCAL_BACKUP_PI_OWNER ;; root:root) owner=$LOCAL_BACKUP_ROOT_OWNER ;; *) return 1 ;; esac
  if [[ -e "$live" || -L "$live" ]]; then
    [[ -f "$live" && ! -L "$live" ]] || { echo "Unsafe deployment destination: $live" >&2; return 1; }
    actual=$(local_backup_hash "$live") || return 1
    metadata=$(local_backup_metadata "$live") || return 1
    IFS='|' read -r old_owner old_mode <<< "$metadata"
    [[ "$old_mode" =~ ^[0-7]{3,4}$ ]] || return 1
  elif [[ "$first" == required || ( "$first" == new && "$expected" != absent ) ]]; then
    echo "Required deployed file is missing: $live" >&2
    return 1
  fi
  if [[ "$mode" == preserve ]]; then
    [[ "$old_owner" == "$owner" ]] || { echo "Disk lifecycle script ownership differs: $live" >&2; return 1; }
    mode=$old_mode
  fi
  case "$policy" in
    revision|conf|cloud|status)
      [[ "$expected" == absent || "$expected" =~ ^[0-9a-f]{64}$ ]] || return 1
      if [[ "$actual" != absent ]]; then
        staged=$(local_backup_hash "$source") || return 1
        [[ "$actual" == "$expected" || "$actual" == "$staged" ]] || {
          echo "Live code differs: $live; inspect before deploying." >&2; return 1;
        }
      fi
      ;;
    preserve|defaults)
      [[ "$expected" == managed ]] || return 1
      if [[ "$actual" != absent ]]; then
        [[ "${old_owner%%:*}" == "${LOCAL_BACKUP_ROOT_OWNER%%:*}" ]] &&
          (( (8#$old_mode & 022) == 0 )) || return 1
      fi
      ;;
    replace) [[ "$expected" == managed ]] || return 1 ;;
    *) return 1 ;;
  esac
  printf '%s|%s|%s|%s|%s\n' "$actual" "$old_owner" "$old_mode" "$owner" "$mode"
}

local_backup_preflight() { # <stage> <pi|tm> <reviewed-hashes>; no live writes
  local stage=$1 profile=$2 previous=$3 plan source destination policy owner mode first metadata
  [[ "$stage" == /* && ! -L "$stage" && -d "$stage" ]] || return 1
  [[ -z "$LOCAL_BACKUP_ROOT" || ( "$LOCAL_BACKUP_ROOT" == /* && "$LOCAL_BACKUP_ROOT" != / ) ]] || return 1
  plan=$(local_backup_plan "$profile") || return 1
  /usr/bin/mkdir -m 0700 "$stage/transaction" || return 1
  : > "$stage/transaction/preflight" || return 1
  while IFS='|' read -r source destination policy owner mode first; do
    metadata=$(local_backup_check_record "$stage/$source" "$LOCAL_BACKUP_ROOT$destination" \
      "$policy" "$owner" "$mode" "$first" "$(local_backup_expected_hash "$previous" "$source")") || return 1
    printf '%s|%s|%s|%s\n' "$source" "$destination" "$policy" "$metadata" >> "$stage/transaction/preflight" || return 1
  done <<< "$plan"
}

local_backup_stage_settings() { # <defaults> <live> <policy> <private-staged-output>
  $LOCAL_BACKUP_SUDO /usr/bin/python3 - "$@" <<'PY'
import json, os, sys
from pathlib import Path
source, live, policy, output = sys.argv[1:]
data = Path(source).read_bytes()
if Path(live).exists():
    data = Path(live).read_bytes()
    if policy == 'defaults':
        current = json.loads(data)
        updated = dict(json.loads(Path(source).read_bytes()), **current)
        if updated != current:
            data = (json.dumps(updated, indent=2) + '\n').encode()
fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'wb') as stream:
    stream.write(data)
    stream.flush()
    os.fsync(stream.fileno())
PY
}

local_backup_prepare_record() { # <preflight-record>; every original is saved before publication
  local source destination policy old_hash old_owner old_mode owner mode live saved replacement temporary
  local new_hash action=replace
  IFS='|' read -r source destination policy old_hash old_owner old_mode owner mode <<< "$1"
  live="$LOCAL_BACKUP_ROOT$destination"
  saved="$LOCAL_BACKUP_STAGE/previous/tree$destination"
  local_backup_snapshot_matches "$live" "$old_hash" "$old_owner" "$old_mode" || return 1
  if [[ "$old_hash" != absent ]]; then
    /usr/bin/mkdir -p -m 0700 "${saved%/*}" || return 1
    $LOCAL_BACKUP_SUDO /usr/bin/cp -p -- "$live" "$saved" || return 1
    local_backup_snapshot_matches "$saved" "$old_hash" "$old_owner" "$old_mode" || return 1
  fi
  replacement="$LOCAL_BACKUP_STAGE/$source"
  if [[ "$policy" == defaults || "$policy" == preserve ]]; then
    replacement="$LOCAL_BACKUP_STAGE/transaction/${source##*/}"
    local_backup_stage_settings "$LOCAL_BACKUP_STAGE/$source" "$saved" "$policy" "$replacement" || return 1
  fi
  new_hash=$(local_backup_hash "$replacement") || return 1
  temporary="$live.deploy.$BASHPID"
  if [[ ( "$policy" == preserve || "$policy" == defaults ) && "$new_hash" == "$old_hash" ]]; then
    action=keep
    temporary=-
  else
    [[ ! -e "$temporary" && ! -L "$temporary" ]] || return 1
    LOCAL_BACKUP_TEMPORARIES+=("$temporary")
    $LOCAL_BACKUP_SUDO /usr/bin/install -o "${owner%%:*}" -g "${owner#*:}" -m "$mode" "$replacement" "$temporary" || return 1
    local_backup_snapshot_matches "$temporary" "$new_hash" "$owner" "$mode" || return 1
  fi
  # Absence is recorded explicitly; rollback never invents an old file.
  printf '%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\n' "$source" "$destination" \
    "$old_hash" "$old_owner" "$old_mode" "$new_hash" "$owner" "$mode" "$action" "$temporary" \
    >> "$LOCAL_BACKUP_STAGE/previous/manifest" || return 1
}

local_backup_prepare() {
  local record
  /usr/bin/mkdir -m 0700 "$LOCAL_BACKUP_STAGE/previous" || return 1
  : > "$LOCAL_BACKUP_STAGE/previous/manifest" || return 1
  while IFS= read -r record; do
    local_backup_prepare_record "$record" || return 1
  done < "$LOCAL_BACKUP_STAGE/transaction/preflight"
  LOCAL_BACKUP_STATE=ready
}

local_backup_publish() {
  local record source destination old_hash old_owner old_mode new_hash owner mode action temporary live
  [[ "$LOCAL_BACKUP_STATE" == ready ]] || return 1
  LOCAL_BACKUP_STATE=publishing
  while IFS= read -r record; do
    IFS='|' read -r source destination old_hash old_owner old_mode new_hash owner mode action temporary <<< "$record"
    live="$LOCAL_BACKUP_ROOT$destination"
    local_backup_snapshot_matches "$live" "$old_hash" "$old_owner" "$old_mode" || {
      echo "Live file changed after preflight: $destination" >&2; return 1;
    }
    [[ "$action" != keep ]] || continue
    # Record before mv: a wrapper or interrupted command can fail after rename.
    LOCAL_BACKUP_PUBLISHED+=("$record")
    printf '%s\n' "$destination" >> "$LOCAL_BACKUP_STAGE/transaction/published" || return 1
    $LOCAL_BACKUP_SUDO /usr/bin/mv -Tf -- "$temporary" "$live" || return 1
    local_backup_snapshot_matches "$live" "$new_hash" "$owner" "$mode" || return 1
  done < "$LOCAL_BACKUP_STAGE/previous/manifest"
  LOCAL_BACKUP_STATE=published
}

local_backup_restore_record() {
  local source destination old_hash old_owner old_mode new_hash owner mode action temporary live saved
  IFS='|' read -r source destination old_hash old_owner old_mode new_hash owner mode action temporary <<< "$1"
  live="$LOCAL_BACKUP_ROOT$destination"
  saved="$LOCAL_BACKUP_STAGE/previous/tree$destination"
  # Do not clobber an unrelated edit that occurred during the failed deploy.
  if local_backup_snapshot_matches "$live" "$old_hash" "$old_owner" "$old_mode"; then
    return 0
  fi
  local_backup_snapshot_matches "$live" "$new_hash" "$owner" "$mode" || return 1
  if [[ "$old_hash" == absent ]]; then
    $LOCAL_BACKUP_SUDO /usr/bin/rm -- "$live" || return 1
  else
    local_backup_snapshot_matches "$saved" "$old_hash" "$old_owner" "$old_mode" || return 1
    temporary="$live.rollback.$BASHPID"
    [[ ! -e "$temporary" && ! -L "$temporary" ]] || return 1
    LOCAL_BACKUP_TEMPORARIES+=("$temporary")
    $LOCAL_BACKUP_SUDO /usr/bin/install -o "${old_owner%%:*}" -g "${old_owner#*:}" -m "$old_mode" "$saved" "$temporary" || return 1
    $LOCAL_BACKUP_SUDO /usr/bin/mv -Tf -- "$temporary" "$live" || return 1
  fi
  local_backup_snapshot_matches "$live" "$old_hash" "$old_owner" "$old_mode"
}

local_backup_rollback() {
  local index failed=0
  for ((index=${#LOCAL_BACKUP_PUBLISHED[@]}-1; index>=0; index--)); do
    local_backup_restore_record "${LOCAL_BACKUP_PUBLISHED[$index]}" || {
      echo "Cannot restore cohort entry $((index+1)); see previous/manifest." >&2
      failed=1
    }
  done
  # Restoring unit bytes is insufficient until systemd reloads those units.
  if (( ${#LOCAL_BACKUP_PUBLISHED[@]} )); then
    $LOCAL_BACKUP_SUDO "$LOCAL_BACKUP_SYSTEMCTL" daemon-reload || failed=1
  fi
  (( failed == 0 ))
}

local_backup_cleanup_temporaries() {
  local path failed=0
  for path in "${LOCAL_BACKUP_TEMPORARIES[@]}"; do
    [[ -e "$path" || -L "$path" ]] || continue
    [[ -f "$path" && ! -L "$path" ]] && $LOCAL_BACKUP_SUDO /usr/bin/rm -- "$path" || failed=1
  done
  (( failed == 0 ))
}
