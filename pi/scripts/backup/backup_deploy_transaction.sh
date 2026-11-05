#!/bin/bash
# File transaction behind backup_local_deploy.sh. The fixed plan is the only
# destination allowlist; no lock inode or runtime backup data is ever replaced.
# Its journal (previous tree, manifest, active timers, marker) lives in the
# persistent state directory, not the stage: /tmp is emptied at boot, and a
# forced kill must leave enough on disk to restore every destination exactly.
LOCAL_BACKUP_CODE_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
. "$LOCAL_BACKUP_CODE_DIR/backup_deploy_trigger.sh" || return 1
LOCAL_BACKUP_SUDO=${LOCAL_BACKUP_SUDO-/usr/bin/sudo -n}
LOCAL_BACKUP_STAT=${LOCAL_BACKUP_STAT:-/usr/bin/stat}
LOCAL_BACKUP_SYSTEMCTL=${LOCAL_BACKUP_SYSTEMCTL:-/usr/bin/systemctl}
LOCAL_BACKUP_ROOT=${LOCAL_BACKUP_ROOT:-}
LOCAL_BACKUP_PI_OWNER=${LOCAL_BACKUP_PI_OWNER:-pi:pi}
LOCAL_BACKUP_ROOT_OWNER=${LOCAL_BACKUP_ROOT_OWNER:-root:root}
LOCAL_BACKUP_TIMERS=(vanpi-icloud-backup.timer vanpi-time-machine-icloud.timer
  vanpi-icloud-backup-resume.timer vanpi-time-machine-icloud-resume.timer)
LOCAL_BACKUP_PROC_LOCKS=${LOCAL_BACKUP_PROC_LOCKS:-/proc/locks}
LOCAL_BACKUP_STAGED_REGISTRY=pi/scripts/backup/backup_lock_registry.sh
LOCAL_BACKUP_SYNC=${LOCAL_BACKUP_SYNC:-/usr/bin/sync}
# Copied into each transaction so its recovery never depends on the live tree.
LOCAL_BACKUP_RECOVERY_CLOSURE=("$BACKUP_DEPLOY_RECOVERY" backup_deploy_state.sh backup_deploy_trigger.sh
  backup_deploy_plan.sh backup_deploy_transaction.sh backup_locks.sh backup_lock_io.sh backup_lock_registry.sh)
# Finished transactions kept beside a new one, for a later manual rollback.
LOCAL_BACKUP_KEEP_TRANSACTIONS=4
LOCAL_BACKUP_TXN=${LOCAL_BACKUP_TXN:-}

local_backup_hash() {
  local digest
  digest=$($LOCAL_BACKUP_SUDO /usr/bin/sha256sum -- "$1") || return 1
  printf '%s\n' "${digest%% *}"
}

local_backup_metadata() {
  $LOCAL_BACKUP_SUDO "$LOCAL_BACKUP_STAT" -c '%U:%G|%a' -- "$1"
}

local_backup_regular() { # Private journal descendants must be inspected as the state owner.
  $LOCAL_BACKUP_SUDO /usr/bin/test -f "$1" && ! $LOCAL_BACKUP_SUDO /usr/bin/test -L "$1"
}

local_backup_exists() {
  $LOCAL_BACKUP_SUDO /usr/bin/test -e "$1" || $LOCAL_BACKUP_SUDO /usr/bin/test -L "$1"
}

local_backup_snapshot_matches() { # <path> <sha256|absent> <owner:group> <mode>
  local path=$1 expected=$2 owner=$3 mode=$4 actual metadata
  if [[ "$expected" == absent ]]; then
    # An argument-less return during EXIT cleanup can reuse the trapped exit
    # status. Report the absence check explicitly, including after removal.
    ! local_backup_exists "$path" || return 1
    return 0
  fi
  local_backup_regular "$path" || return 1
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

local_backup_staged_lock_names() { # <stage>: <class>-<name> per line, from the registry being published
  /bin/bash -c '
    . "$1" || exit 1
    for name in "${BACKUP_JOB_KINDS[@]}"; do printf "job-%s\n" "$name"; done
    for name in "${BACKUP_DISK_LABELS[@]}"; do printf "disk-%s\n" "$name"; done
    for name in "${BACKUP_DATA_NAMES[@]}"; do printf "data-%s\n" "$name"; done
    [[ -z ${BACKUP_CLOUD_SLOT:-} ]] || printf "cloud-%s\n" "$BACKUP_CLOUD_SLOT"
  ' registry "$1/$LOCAL_BACKUP_STAGED_REGISTRY"
}

local_backup_registry_guard() { # <stage>: under the fence; refuse to unregister a lock held now
  # Drain, stop and status code find jobs only through the registry, so
  # publishing one without a held entry (e.g. a rollback during an offsite
  # run) would hide that holder. /proc/locks names holders without touching
  # them: even a momentary probe flock fails a starting job's shared disk hold.
  local stage=$1 staged name path identity
  local -a fields
  local -A known=() held=()
  [[ -e "$BACKUP_LOCK_DIR" || -L "$BACKUP_LOCK_DIR" ]] || return 0
  if [[ -L "$BACKUP_LOCK_DIR" || ! -d "$BACKUP_LOCK_DIR" ]] ||
     ! backup_lock_metadata_ok "$BACKUP_LOCK_DIR" "$BACKUP_LOCK_DIR_MODE"; then
    echo "Backup lock directory is unsafe: $BACKUP_LOCK_DIR" >&2
    return 1
  fi
  staged=$(local_backup_staged_lock_names "$stage") || { echo 'Cannot read the staged lock registry.' >&2; return 1; }
  while IFS= read -r name; do
    [[ -z "$name" ]] || known[$name]=1
  done <<< "$staged"
  [[ -r "$LOCAL_BACKUP_PROC_LOCKS" ]] || { echo "Cannot read $LOCAL_BACKUP_PROC_LOCKS." >&2; return 1; }
  # Holder records read "N: FLOCK ADVISORY WRITE <pid> <hex major>:<hex minor>:<inode> ...";
  # "->" marks a waiter, and "<none>" records have no inode to match.
  while read -r -a fields; do
    [[ "${fields[1]:-}" != '->' && "${fields[5]:-}" =~ ^([0-9a-f]+):([0-9a-f]+):([0-9]+)$ ]] || continue
    held["$((16#${BASH_REMATCH[1]})):$((16#${BASH_REMATCH[2]})):${BASH_REMATCH[3]}"]=1
  done < "$LOCAL_BACKUP_PROC_LOCKS"
  for path in "$BACKUP_LOCK_DIR"/*.lock; do
    [[ -f "$path" && ! -L "$path" ]] || continue
    identity=$("$LOCAL_BACKUP_STAT" -c '%Hd:%Ld:%i' -- "$path") || return 1
    [[ -n ${held[$identity]:-} ]] || continue
    name=${path##*/}
    name=${name%.lock}
    [[ -n ${known[$name]:-} ]] || {
      echo "Refusing to publish a lock registry without $name while it is held; stop that job first." >&2
      return 1
    }
  done
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
  saved="$LOCAL_BACKUP_TXN/previous/tree$destination"
  local_backup_snapshot_matches "$live" "$old_hash" "$old_owner" "$old_mode" || return 1
  if [[ "$old_hash" != absent ]]; then
    $LOCAL_BACKUP_SUDO /usr/bin/mkdir -p -m 0700 "${saved%/*}" || return 1
    $LOCAL_BACKUP_SUDO /usr/bin/cp -p -- "$live" "$saved" || return 1
    local_backup_snapshot_matches "$saved" "$old_hash" "$old_owner" "$old_mode" || return 1
  fi
  replacement="$LOCAL_BACKUP_STAGE/$source"
  if [[ "$policy" == defaults || "$policy" == preserve ]]; then
    replacement="$LOCAL_BACKUP_STAGE/transaction/${source##*/}"
    local_backup_stage_settings "$LOCAL_BACKUP_STAGE/$source" "$saved" "$policy" "$replacement" || return 1
  fi
  new_hash=$(local_backup_hash "$replacement") || return 1
  # Fixed per-run names let recovery find temporaries a killed run never recorded.
  temporary="$live.deploy.$LOCAL_BACKUP_TEMP_ID"
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
    >> "$LOCAL_BACKUP_STAGE/transaction/manifest" || return 1
}

local_backup_prepare() {
  local record
  $LOCAL_BACKUP_SUDO /usr/bin/install -d -o "${BACKUP_DEPLOY_STATE_OWNER%%:*}" \
    -g "${BACKUP_DEPLOY_STATE_OWNER#*:}" -m 0700 "$LOCAL_BACKUP_TXN/previous" || return 1
  : > "$LOCAL_BACKUP_STAGE/transaction/manifest" || return 1
  while IFS= read -r record; do
    local_backup_prepare_record "$record" || return 1
  done < "$LOCAL_BACKUP_STAGE/transaction/preflight"
  local_backup_install_private "$LOCAL_BACKUP_STAGE/transaction/manifest" "$LOCAL_BACKUP_TXN/previous/manifest" || return 1
  # Only a complete, durable manifest may switch the marker to publishing.
  local_backup_marker_write publishing || return 1
  LOCAL_BACKUP_STATE=ready
}

local_backup_publish() {
  local record records source destination old_hash old_owner old_mode new_hash owner mode action temporary live
  [[ "$LOCAL_BACKUP_STATE" == ready ]] || return 1
  records=$($LOCAL_BACKUP_SUDO /bin/cat -- "$LOCAL_BACKUP_TXN/previous/manifest") || return 1
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
  done <<< "$records"
  LOCAL_BACKUP_STATE=published
}

local_backup_restore_record() {
  local source destination old_hash old_owner old_mode new_hash owner mode action temporary live saved
  IFS='|' read -r source destination old_hash old_owner old_mode new_hash owner mode action temporary <<< "$1"
  live="$LOCAL_BACKUP_ROOT$destination"
  saved="$LOCAL_BACKUP_TXN/previous/tree$destination"
  # Do not clobber an unrelated edit that occurred during the failed deploy.
  if local_backup_snapshot_matches "$live" "$old_hash" "$old_owner" "$old_mode"; then
    return 0
  fi
  local_backup_snapshot_matches "$live" "$new_hash" "$owner" "$mode" || return 1
  if [[ "$old_hash" == absent ]]; then
    $LOCAL_BACKUP_SUDO /usr/bin/rm -- "$live" || return 1
  else
    local_backup_snapshot_matches "$saved" "$old_hash" "$old_owner" "$old_mode" || return 1
    temporary="$live.rollback.$LOCAL_BACKUP_TEMP_ID"
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

local_backup_flush_destinations() { # Persist live inodes AND their directory entries before a decision.
  local records source destination rest live
  [[ -n "$LOCAL_BACKUP_TXN" ]] || return 0
  local_backup_exists "$LOCAL_BACKUP_TXN/previous/manifest" || return 0
  records=$($LOCAL_BACKUP_SUDO /bin/cat -- "$LOCAL_BACKUP_TXN/previous/manifest") || return 1
  while IFS='|' read -r source destination rest; do
    live=$LOCAL_BACKUP_ROOT$destination
    if local_backup_exists "$live"; then
      local_backup_regular "$live" || return 1
      $LOCAL_BACKUP_SUDO "$LOCAL_BACKUP_SYNC" -- "$live" || return 1
    fi
    $LOCAL_BACKUP_SUDO "$LOCAL_BACKUP_SYNC" -- "${live%/*}" || return 1
  done <<< "$records"
}

local_backup_verify_published() {
  local records source destination old_hash old_owner old_mode new_hash owner mode rest
  records=$($LOCAL_BACKUP_SUDO /bin/cat -- "$LOCAL_BACKUP_TXN/previous/manifest") || return 1
  while IFS='|' read -r source destination old_hash old_owner old_mode new_hash owner mode rest; do
    local_backup_snapshot_matches "$LOCAL_BACKUP_ROOT$destination" "$new_hash" "$owner" "$mode" || return 1
  done <<< "$records"
}

local_backup_restart_timers() { # every timer active before the fence; tries them all
  local timer failed=0
  for timer in "${LOCAL_BACKUP_ACTIVE_TIMERS[@]}"; do
    $LOCAL_BACKUP_SUDO "$LOCAL_BACKUP_SYSTEMCTL" start "$timer" || failed=1
  done
  (( failed == 0 ))
}

local_backup_install_private() { # <source> <journal path>: a verified copy only the state owner can read
  local expected
  expected=$(local_backup_hash "$1") || return 1
  $LOCAL_BACKUP_SUDO /usr/bin/install -o "${BACKUP_DEPLOY_STATE_OWNER%%:*}" \
    -g "${BACKUP_DEPLOY_STATE_OWNER#*:}" -m 0600 -- "$1" "$2" || return 1
  local_backup_snapshot_matches "$2" "$expected" "$BACKUP_DEPLOY_STATE_OWNER" 600
}

local_backup_state_directory() { # create the fixed state directory once; refuse an unsafe one
  if [[ ! -e "$BACKUP_DEPLOY_STATE_DIR" && ! -L "$BACKUP_DEPLOY_STATE_DIR" ]]; then
    $LOCAL_BACKUP_SUDO /usr/bin/install -d -o "${BACKUP_DEPLOY_STATE_OWNER%%:*}" \
      -g "${BACKUP_DEPLOY_STATE_OWNER#*:}" -m 0755 "$BACKUP_DEPLOY_STATE_DIR" || return 1
  fi
  backup_deploy_owned "$BACKUP_DEPLOY_STATE_DIR" d || {
    echo "Backup deployment state directory is unsafe: $BACKUP_DEPLOY_STATE_DIR" >&2; return 1;
  }
}

local_backup_prune_transactions() { # under the fence with nothing recorded: drop the oldest records
  local path index
  local -a finished=()
  for path in "$BACKUP_DEPLOY_STATE_DIR"/*; do
    [[ "${path##*/}" =~ $BACKUP_DEPLOY_ID_PATTERN ]] || continue
    backup_deploy_owned "$path" d || { echo "Unsafe deployment record: $path" >&2; return 1; }
    finished+=("$path")
  done
  # Ids begin with a fixed-width UTC timestamp, so glob order is chronological.
  for ((index = 0; index + LOCAL_BACKUP_KEEP_TRANSACTIONS < ${#finished[@]}; index++)); do
    $LOCAL_BACKUP_SUDO /usr/bin/rm -r --one-file-system -- "${finished[$index]}" || return 1
  done
}

local_backup_journal_open() { # <pi|tm>: durable transaction record before any live change
  local name recovery
  [[ "$LOCAL_BACKUP_ROOT" != *$'\n'* ]] || return 1
  local_backup_state_directory || return 1
  local_backup_prune_transactions || return 1
  LOCAL_BACKUP_PROFILE=$1
  LOCAL_BACKUP_TXN=$BACKUP_DEPLOY_STATE_DIR/$(/usr/bin/date -u +%Y%m%dT%H%M%SZ)-$LOCAL_BACKUP_TEMP_ID
  recovery=$LOCAL_BACKUP_TXN/$BACKUP_DEPLOY_RECOVERY_DIR
  [[ ! -e "$LOCAL_BACKUP_TXN" && ! -L "$LOCAL_BACKUP_TXN" ]] || return 1
  $LOCAL_BACKUP_SUDO /usr/bin/install -d -o "${BACKUP_DEPLOY_STATE_OWNER%%:*}" \
    -g "${BACKUP_DEPLOY_STATE_OWNER#*:}" -m 0700 "$LOCAL_BACKUP_TXN" "$recovery" || return 1
  for name in "${LOCAL_BACKUP_RECOVERY_CLOSURE[@]}"; do
    local_backup_install_private "$LOCAL_BACKUP_CODE_DIR/$name" "$recovery/$name" || return 1
  done
  printf '%s\n' "${LOCAL_BACKUP_ACTIVE_TIMERS[@]}" > "$LOCAL_BACKUP_STAGE/transaction/active-timers" || return 1
  local_backup_install_private "$LOCAL_BACKUP_STAGE/transaction/active-timers" "$LOCAL_BACKUP_TXN/active-timers" || return 1
  local_backup_marker_write preparing
}

local_backup_marker_write() { # <preparing|publishing|committed>: journal durable before the decision
  local working=$LOCAL_BACKUP_STAGE/transaction/marker temporary=$BACKUP_DEPLOY_MARKER.$LOCAL_BACKUP_TEMP_ID
  printf 'version=%s\nid=%s\nphase=%s\nprofile=%s\ntemp=%s\nroot=%s\n' "$BACKUP_DEPLOY_MARKER_VERSION" \
    "${LOCAL_BACKUP_TXN##*/}" "$1" "$LOCAL_BACKUP_PROFILE" "$LOCAL_BACKUP_TEMP_ID" "$LOCAL_BACKUP_ROOT" \
    > "$working" || return 1
  [[ ! -e "$temporary" && ! -L "$temporary" ]] || return 1
  LOCAL_BACKUP_TEMPORARIES+=("$temporary")
  $LOCAL_BACKUP_SUDO /usr/bin/install -o "${BACKUP_DEPLOY_STATE_OWNER%%:*}" \
    -g "${BACKUP_DEPLOY_STATE_OWNER#*:}" -m 0644 -- "$working" "$temporary" || return 1
  # syncfs: the journal, its previous tree and this marker reach the disk before the rename.
  $LOCAL_BACKUP_SUDO "$LOCAL_BACKUP_SYNC" -f -- "$temporary" || return 1
  $LOCAL_BACKUP_SUDO /usr/bin/mv -Tf -- "$temporary" "$BACKUP_DEPLOY_MARKER" || return 1
  # Once the commit decision is visible, even a failed fsync must not make the
  # EXIT trap roll back a cohort the recorded recoverer will instead finalize.
  [[ "$1" != committed ]] || LOCAL_BACKUP_STATE=committed
  $LOCAL_BACKUP_SUDO "$LOCAL_BACKUP_SYNC" -- "$BACKUP_DEPLOY_STATE_DIR"
}

local_backup_journal_close() { # remove this transaction's marker durably; never another's
  local status
  [[ -n "$LOCAL_BACKUP_TXN" ]] || return 0
  backup_deploy_marker_read
  status=$?
  (( status != 1 )) || return 0
  (( status == 0 )) && [[ "$BACKUP_DEPLOY_STATE_DIR/$BACKUP_DEPLOY_ID" == "$LOCAL_BACKUP_TXN" ]] || return 1
  local_backup_flush_destinations || return 1
  $LOCAL_BACKUP_SUDO /usr/bin/rm -- "$BACKUP_DEPLOY_MARKER" || return 1
  $LOCAL_BACKUP_SUDO "$LOCAL_BACKUP_SYNC" -- "$BACKUP_DEPLOY_STATE_DIR" || return 1
  # A later transaction's failure deserves its own alert; a stale stamp only delays one.
  [[ ! -e "$BACKUP_DEPLOY_ALERTED" ]] || $LOCAL_BACKUP_SUDO /usr/bin/rm -f -- "$BACKUP_DEPLOY_ALERTED" || true
}
