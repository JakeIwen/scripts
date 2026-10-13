#!/bin/bash
# Conservative root-filesystem cache cleanup. Dry-run unless --apply is given.
set -u

apply=0
case ${1:-} in
  "") ;;
  --apply) apply=1 ;;
  -h|--help)
    echo "usage: rootfs_tidy.sh [--apply]"
    exit 0
    ;;
  *)
    echo "usage: rootfs_tidy.sh [--apply]" >&2
    exit 2
    ;;
esac

standalone=${ROOTFS_TIDY_CODEX_STANDALONE:-/home/pi/.codex/packages/standalone}
releases="$standalone/releases"
current="$standalone/current"
install_lock="$standalone/install.lock"
flock_command=${ROOTFS_TIDY_FLOCK:-/usr/bin/flock}
findmnt_command=${ROOTFS_TIDY_FINDMNT:-/usr/bin/findmnt}
stat_command=${ROOTFS_TIDY_STAT:-/usr/bin/stat}
device_stat_command=${ROOTFS_TIDY_DEVICE_STAT:-$stat_command}
rm_command=${ROOTFS_TIDY_RM:-/bin/rm}
id_command=${ROOTFS_TIDY_ID:-/usr/bin/id}
sudo_command=${ROOTFS_TIDY_SUDO:-/usr/bin/sudo}
apt_get_command=${ROOTFS_TIDY_APT_GET:-/usr/bin/apt-get}

refuse() {
  echo "refusing rootfs tidy: $*" >&2
  exit 1
}

release_mtime() {
  local path=$1 value
  value=$("$stat_command" -c '%Y' -- "$path" 2>/dev/null) ||
    value=$("$stat_command" -f '%m' -- "$path" 2>/dev/null) || return 1
  [[ "$value" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$value"
}

# findmnt -r escapes whitespace, backslashes and non-printables in TARGET, so
# literal prefix comparison is only sound for paths made of these characters.
findmnt_literal_path() {
  [[ "$1" =~ ^[A-Za-z0-9._+/-]+$ ]]
}

validate_release_child() {
  local target=$1 name resolved
  [[ -d "$target" && ! -L "$target" ]] || return 1
  name=${target##*/}
  [[ -n "$name" && "$target" == "$releases/$name" ]] || return 1
  [[ "$name" != "." && "$name" != ".." ]] && findmnt_literal_path "$name" || return 1
  resolved=$(cd -P -- "$target" 2>/dev/null && pwd) || return 1
  [[ "$resolved" == "$releases_resolved/$name" ]]
}

path_device() {
  local path=$1 value
  value=$("$device_stat_command" -c '%d' -- "$path" 2>/dev/null) ||
    value=$("$device_stat_command" -f '%d' -- "$path" 2>/dev/null) || return 1
  [[ "$value" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$value"
}

verify_delete_boundary() {
  local target=$1 target_resolved releases_device target_device
  local mount_output mount_target parsed_mounts=0

  target_resolved=$(cd -P -- "$target" 2>/dev/null && pwd) || {
    echo "cannot resolve release before deleting: $target" >&2
    return 1
  }
  [[ "$target_resolved" == "$releases_resolved/${target##*/}" ]] || {
    echo "release resolution changed before deleting: $target" >&2
    return 1
  }
  if ! findmnt_literal_path "$target" || ! findmnt_literal_path "$target_resolved"; then
    echo "release path has characters mount discovery escapes: $target" >&2
    return 1
  fi
  releases_device=$(path_device "$releases_resolved") || {
    echo "cannot identify releases filesystem: $releases_resolved" >&2
    return 1
  }
  target_device=$(path_device "$target_resolved") || {
    echo "cannot identify release filesystem: $target_resolved" >&2
    return 1
  }
  if [[ "$target_device" != "$releases_device" ]]; then
    echo "release is on a different filesystem from releases/: $target" >&2
    return 1
  fi

  mount_output=$("$findmnt_command" -rn -o TARGET 2>/dev/null) || {
    echo "mount discovery failed before deleting: $target" >&2
    return 1
  }
  while IFS= read -r mount_target; do
    [[ -n "$mount_target" ]] || continue
    if [[ "$mount_target" != /* ]]; then
      echo "mount discovery returned an unparseable target" >&2
      return 1
    fi
    parsed_mounts=$((parsed_mounts + 1))
    if [[ "$mount_target" == "$target" || "$mount_target" == "$target/"* ||
          "$mount_target" == "$target_resolved" ||
          "$mount_target" == "$target_resolved/"* ]]; then
      echo "mount point exists at or beneath release: $mount_target" >&2
      return 1
    fi
  done <<< "$mount_output"
  if (( parsed_mounts == 0 )); then
    echo "mount discovery returned no parseable targets" >&2
    return 1
  fi
}

run_apt_clean() {
  local uid
  uid=$("$id_command" -u 2>/dev/null) || {
    echo "apt cache: skipped (cannot determine effective user)"
    return 0
  }
  if [[ "$uid" == 0 ]]; then
    if (( apply )); then
      "$apt_get_command" clean
    else
      echo "apt cache: would run apt-get clean as root"
    fi
    return
  fi
  if "$sudo_command" -n true >/dev/null 2>&1; then
    if (( apply )); then
      "$sudo_command" -n "$apt_get_command" clean
    else
      echo "apt cache: would run sudo -n apt-get clean"
    fi
  else
    echo "apt cache: skipped (root or passwordless sudo required)"
  fi
}

[[ -d "$standalone" && ! -L "$standalone" ]] ||
  refuse "Codex standalone directory is missing or unsafe: $standalone"
[[ -d "$releases" && ! -L "$releases" ]] ||
  refuse "Codex releases directory is missing or unsafe: $releases"
[[ -f "$install_lock" && ! -L "$install_lock" ]] ||
  refuse "Codex install lock is missing or unsafe: $install_lock"
[[ -x "$flock_command" ]] || refuse "flock is unavailable: $flock_command"
[[ -x "$findmnt_command" ]] || refuse "findmnt is unavailable: $findmnt_command"
[[ -x "$device_stat_command" ]] ||
  refuse "device stat is unavailable: $device_stat_command"

# Hold the installer's own lock across discovery and deletion so a release cannot
# become current after it has been selected for pruning.
exec 9<>"$install_lock" || refuse "cannot open Codex install lock"
"$flock_command" -n 9 || refuse "Codex install lock is held"

[[ -L "$current" ]] || refuse "Codex current is not a symlink"
releases_resolved=$(cd -P -- "$releases" 2>/dev/null && pwd) ||
  refuse "cannot resolve Codex releases directory"
current_resolved=$(cd -P -- "$current" 2>/dev/null && pwd) ||
  refuse "Codex current does not resolve to a directory"
[[ "${current_resolved%/*}" == "$releases_resolved" ]] ||
  refuse "Codex current resolves outside releases: $current_resolved"
validate_release_child "$releases/${current_resolved##*/}" ||
  refuse "Codex current is not a direct release child"

release_paths=()
newest_other=
newest_other_mtime=-1
shopt -s nullglob
for candidate in "$releases"/*; do
  [[ -d "$candidate" && ! -L "$candidate" ]] || continue
  validate_release_child "$candidate" ||
    refuse "unsafe Codex release path: $candidate"
  mtime=$(release_mtime "$candidate") ||
    refuse "cannot read Codex release timestamp: $candidate"
  release_paths+=("$candidate")
  [[ "$candidate" == "$releases/${current_resolved##*/}" ]] && continue
  if (( mtime > newest_other_mtime )) ||
    { (( mtime == newest_other_mtime )) && [[ "$candidate" > "$newest_other" ]]; }; then
    newest_other=$candidate
    newest_other_mtime=$mtime
  fi
done

prune_targets=()
for candidate in "${release_paths[@]}"; do
  [[ "$candidate" == "$releases/${current_resolved##*/}" ||
     "$candidate" == "$newest_other" ]] && continue
  validate_release_child "$candidate" ||
    refuse "unsafe Codex prune target: $candidate"
  prune_targets+=("$candidate")
done

if (( ${#prune_targets[@]} == 0 )); then
  echo "Codex releases: nothing to prune"
elif (( apply )); then
  for target in "${prune_targets[@]}"; do
    validate_release_child "$target" ||
      refuse "Codex prune target changed before removal: $target"
    [[ "$target" != "$releases/${current_resolved##*/}" &&
       "$target" != "$newest_other" ]] ||
      refuse "protected Codex release selected for removal: $target"
    verify_delete_boundary "$target" ||
      refuse "mount/filesystem safety check failed for: $target"
    echo "Codex releases: removing $target"
    "$rm_command" -rf --one-file-system -- "$target" ||
      refuse "could not remove Codex release: $target"
  done
else
  for target in "${prune_targets[@]}"; do
    echo "Codex releases: would remove $target"
  done
fi

run_apt_clean
