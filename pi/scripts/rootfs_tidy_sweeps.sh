# shellcheck shell=bash
# Age-based SD-root sweeps sourced by rootfs_tidy.sh (it owns $apply, $failed
# and the dry-run/--apply contract). Every function prints what it would do or
# did; a failure sets failed=1 and leaves the source in place.
#
# Roots and tools are overridable so tests run against a fixture tree.
tidy_home=${ROOTFS_TIDY_HOME:-/home/pi}
archive_mount=${ROOTFS_TIDY_ARCHIVE_MOUNT:-/mnt/EXFAT512}
archive_root=${ROOTFS_TIDY_ARCHIVE_ROOT:-$archive_mount/vanpi-root-archive}
mountpoint_command=${ROOTFS_TIDY_MOUNTPOINT:-/usr/bin/mountpoint}
codex_archive_days=${ROOTFS_TIDY_CODEX_DAYS:-30}
obd_tmp=${ROOTFS_TIDY_OBD_TMP:-$tidy_home/dev/obd-things/tmp}
suite_days=${ROOTFS_TIDY_SUITE_DAYS:-2}
playwright_days=${ROOTFS_TIDY_PLAYWRIGHT_DAYS:-14}
cache_days=${ROOTFS_TIDY_CACHE_DAYS:-30}
agent_shared=${ROOTFS_TIDY_AGENT_SHARED:-$tidy_home/Documents/agent-shared}
big_file_bytes=${ROOTFS_TIDY_BIG_BYTES:-209715200}
big_file_days=${ROOTFS_TIDY_BIG_DAYS:-14}

if [[ -n ${ROOTFS_TIDY_SHA256:-} ]]; then
  sha_command=("$ROOTFS_TIDY_SHA256")
elif [[ -x /usr/bin/sha256sum ]]; then
  sha_command=(/usr/bin/sha256sum)
else
  sha_command=(/usr/bin/shasum -a 256)
fi

sha256_of() {
  "${sha_command[@]}" -- "$1" 2>/dev/null | cut -d' ' -f1
}

# Succeeds only when the archive disk is positively a mountpoint. mountpoint
# exits 1 for "not a mountpoint" and other codes for errors; neither proves the
# disk is there, so both skip.
archive_is_mounted() {
  local status=0
  "$mountpoint_command" -q -- "$archive_mount" 2>/dev/null || status=$?
  case $status in
    0) return 0 ;;
    1) echo "Codex archive: skipped ($archive_mount is not a mountpoint)" ;;
    *) echo "Codex archive: skipped (cannot determine whether $archive_mount is mounted, status $status)" ;;
  esac
  return 1
}

# Copy src to dest as a plain file (exFAT keeps no owner or links), verify the
# sha256, then publish and delete the source. An existing differing dest is
# never overwritten.
archive_one_file() {
  local src=$1 dest=$2 part want got
  want=$(sha256_of "$src")
  [[ -n "$want" ]] || { echo "Codex archive: cannot hash $src" >&2; return 1; }
  if [[ -e "$dest" ]]; then
    got=$(sha256_of "$dest")
    if [[ "$got" != "$want" ]]; then
      echo "Codex archive: $dest exists with different content; kept $src" >&2
      return 1
    fi
  else
    /bin/mkdir -p -- "$(dirname -- "$dest")" || return 1
    part="$dest.tidy-part"
    if ! /bin/cp -- "$src" "$part" || ! /usr/bin/touch -r "$src" "$part"; then
      /bin/rm -f -- "$part"
      echo "Codex archive: copy of $src failed" >&2
      return 1
    fi
    got=$(sha256_of "$part")
    if [[ "$got" != "$want" ]]; then
      /bin/rm -f -- "$part"
      echo "Codex archive: checksum mismatch for $src; kept it" >&2
      return 1
    fi
    /bin/mv -- "$part" "$dest" || return 1
  fi
  /bin/rm -f -- "$src"
}

archive_old_codex_files() {
  local root rel src count=0 moved=0
  local codex_dir=$tidy_home/.codex
  archive_is_mounted || return 0
  # Live sessions/ is deliberately absent from this list.
  for root in "$codex_dir/archived_sessions" "$codex_dir/migration-backups"; do
    [[ -d "$root" && ! -L "$root" ]] || continue
    while IFS= read -r -d '' src; do
      rel=${src#"$codex_dir"/}
      count=$((count + 1))
      if (( ! apply )); then
        echo "Codex archive: would move $rel"
      elif archive_one_file "$src" "$archive_root/codex/$rel"; then
        moved=$((moved + 1))
      else
        failed=1
      fi
    done < <(/usr/bin/find "$root" -type f -mmin +$((codex_archive_days * 1440)) -print0)
    (( apply )) && /usr/bin/find "$root" -mindepth 1 -type d -empty \
      -mmin +$((codex_archive_days * 1440)) -delete 2>/dev/null
  done
  echo "Codex archive: $count file(s) older than $codex_archive_days days, $moved moved"
}

# Remove a throwaway directory only when nothing inside it changed within the
# window, since a directory's own mtime ignores nested writes.
remove_idle_tmp_dir() {
  local dir=$1 days=$2
  [[ "$dir" == "$obd_tmp"/?* && -d "$dir" && ! -L "$dir" ]] || return 1
  if [[ -n $(/usr/bin/find "$dir" -mmin -$((days * 1440)) -print -quit) ]]; then
    echo "obd tmp: kept ${dir#"$obd_tmp"/} (recent writes inside)"
    return 0
  fi
  if (( apply )); then
    /bin/rm -rf -- "$dir" || { failed=1; return 1; }
    echo "obd tmp: removed ${dir#"$obd_tmp"/}"
  else
    echo "obd tmp: would remove ${dir#"$obd_tmp"/}"
  fi
}

# Only patterns obd-things' own tooling treats as scratch: pytest suite runs
# (suite-*) and sibling playwright-* caches. tmp/playwright itself, compute,
# vehicle_data and captures are never matched.
sweep_obd_tmp() {
  local dir
  if [[ ! -d "$obd_tmp" || -L "$obd_tmp" ]]; then
    echo "obd tmp: skipped ($obd_tmp is not a directory)"
    return 0
  fi
  while IFS= read -r -d '' dir; do
    remove_idle_tmp_dir "$dir" "$suite_days"
  done < <(/usr/bin/find "$obd_tmp" -mindepth 1 -maxdepth 1 -type d -name 'suite-*' -print0)
  while IFS= read -r -d '' dir; do
    remove_idle_tmp_dir "$dir" "$playwright_days"
  done < <(/usr/bin/find "$obd_tmp" -mindepth 1 -maxdepth 1 -type d -name 'playwright?*' -print0)
}

# Content-addressed download caches only (a missing file is a refetch). Never
# ~/.cache wholesale: it also holds state such as hook-trust and Firefox data.
sweep_old_cache_files() {
  local dir label count=0
  for dir in "$tidy_home/.npm/_cacache" "$tidy_home/.cache/pip"; do
    label=${dir#"$tidy_home"/}
    [[ -d "$dir" && ! -L "$dir" ]] || continue
    count=$(/usr/bin/find "$dir" -type f -mmin +$((cache_days * 1440)) | wc -l | tr -d ' ')
    if (( ! apply )); then
      echo "cache: would delete $count file(s) older than $cache_days days in $label"
    elif /usr/bin/find "$dir" -type f -mmin +$((cache_days * 1440)) -delete; then
      echo "cache: deleted $count file(s) older than $cache_days days in $label"
    else
      echo "cache: cleanup of $label failed" >&2
      failed=1
    fi
  done
}

# Report only: these are user media and evidence, so a person decides.
report_big_agent_shared() {
  local status=0 found
  [[ -d "$agent_shared" ]] || { echo "agent-shared: skipped (no $agent_shared)"; return 0; }
  found=$(/usr/bin/find "$agent_shared" -type f -size +"${big_file_bytes}c" \
    -mmin +$((big_file_days * 1440)) -exec /bin/ls -l {} + 2>/dev/null) || status=$?
  echo "agent-shared: files over $((big_file_bytes / 1048576)) MB and older than $big_file_days days (report only)"
  [[ -z "$found" ]] || printf '%s\n' "$found"
  (( status == 0 )) || echo "agent-shared: some paths could not be read (find status $status); the list may be incomplete"
}

run_extra_sweeps() {
  archive_old_codex_files
  sweep_obd_tmp
  sweep_old_cache_files
  report_big_agent_shared
}
