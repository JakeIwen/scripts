# shellcheck shell=bash
# Age-based SD-root sweeps for rootfs_tidy.sh (it owns the dry-run/--apply
# contract). As root, rootfs_tidy.sh runs this file as the data owner
# (`bash rootfs_tidy_sweeps.sh <apply 0|1>` under setpriv), so a symlink swap
# or racing writer can only reach that user's own files. Shell is enough for
# the move step on that basis: every destructive step re-verifies identity.
# Every failure sets failed=1, keeps the source, and the run exits nonzero.
#
# Roots and tools are overridable so tests run against a fixture tree.
tidy_home=${ROOTFS_TIDY_HOME:-/home/pi}
archive_mount=${ROOTFS_TIDY_ARCHIVE_MOUNT:-/mnt/EXFAT512}
# Expected identity of the archive volume (blkid/findmnt, read live 2026-11-14).
archive_fstype=${ROOTFS_TIDY_ARCHIVE_FSTYPE:-exfat}
archive_uuid=${ROOTFS_TIDY_ARCHIVE_UUID:-122C-A219}
findmnt_command=${ROOTFS_TIDY_FINDMNT:-/usr/bin/findmnt}
codex_archive_days=${ROOTFS_TIDY_CODEX_DAYS:-30}
suite_days=${ROOTFS_TIDY_SUITE_DAYS:-2}
playwright_days=${ROOTFS_TIDY_PLAYWRIGHT_DAYS:-14}
cache_days=${ROOTFS_TIDY_CACHE_DAYS:-30}
big_file_bytes=${ROOTFS_TIDY_BIG_BYTES:-209715200}
big_file_days=${ROOTFS_TIDY_BIG_DAYS:-14}

physical_path() { (cd -P -- "$1" 2>/dev/null && pwd -P); }

# Resolve the home once; every default root hangs off the physical path.
tidy_home=$(physical_path "$tidy_home") || tidy_home=/nonexistent-tidy-home
archive_root=${ROOTFS_TIDY_ARCHIVE_ROOT:-$archive_mount/vanpi-root-archive}
obd_tmp=${ROOTFS_TIDY_OBD_TMP:-$tidy_home/dev/obd-things/tmp}
agent_shared=${ROOTFS_TIDY_AGENT_SHARED:-$tidy_home/Documents/agent-shared}

# A root is usable only when it is a real directory whose physical path is the
# configured one, i.e. no symlink in it or any ancestor.
require_plain_dir() {
  local dir=$1 label=$2 physical
  if [[ -d "$dir" && ! -L "$dir" ]] && physical=$(physical_path "$dir") &&
     [[ "$physical" == "$dir" ]]; then
    return 0
  fi
  echo "$label: skipped ($dir is missing or reaches it through a symlink)"
  return 1
}

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

# device:inode:size:mtime to the sub-second, for "unchanged since" checks.
if /usr/bin/stat -c %s /dev/null >/dev/null 2>&1; then
  file_identity() { /usr/bin/stat -c '%d:%i:%s:%.9Y' -- "$1" 2>/dev/null; }
else
  file_identity() { /usr/bin/stat -f '%d:%i:%z:%Fm' -- "$1" 2>/dev/null; }
fi

# Succeeds only when path lives on the exact archive volume mounted at
# archive_mount. findmnt errors and any other volume are both refusals.
on_archive_volume() {
  local line
  line=$("$findmnt_command" -n -o TARGET,FSTYPE,UUID -T "$1" 2>/dev/null) || return 1
  [[ "$line" == "$archive_mount $archive_fstype $archive_uuid" ]]
}

archive_ready() {
  if ! on_archive_volume "$archive_mount"; then
    echo "Codex archive: skipped ($archive_mount is not the expected $archive_fstype volume $archive_uuid)"
    return 1
  fi
  require_plain_dir "$archive_mount" "Codex archive"
}

# Copy src to dest as a plain file (exFAT keeps no owner or links), verify,
# publish without clobbering, and delete the source only while it is still the
# file that was copied and the archive is still on the expected volume.
archive_one_file() {
  local src=$1 dest=$2 tmpdir=$archive_root/.tidy-tmp tmp ident want got
  ident=$(file_identity "$src")
  want=$(sha256_of "$src")
  if [[ -z "$ident" || -z "$want" ]]; then
    echo "Codex archive: cannot stat or hash $src; kept it" >&2
    return 1
  fi
  if [[ -e "$dest" ]]; then
    got=$(sha256_of "$dest")
    if [[ "$got" != "$want" ]]; then
      echo "Codex archive: $dest exists with different content; kept $src" >&2
      return 1
    fi
  else
    /bin/mkdir -p -- "$tmpdir" "$(dirname -- "$dest")" || return 1
    tmp=$(/usr/bin/mktemp "$tmpdir/copy.XXXXXX") || return 1
    if ! /bin/cp -- "$src" "$tmp" || ! /usr/bin/touch -r "$src" "$tmp"; then
      /bin/rm -f -- "$tmp"
      echo "Codex archive: copy of $src failed; kept it" >&2
      return 1
    fi
    got=$(sha256_of "$tmp")
    if [[ "$got" != "$want" ]]; then
      /bin/rm -f -- "$tmp"
      echo "Codex archive: checksum mismatch for $src; kept it" >&2
      return 1
    fi
    # -n never replaces an existing file; a surviving temp means it refused.
    /bin/mv -n -- "$tmp" "$dest"
    if [[ -e "$tmp" ]]; then
      /bin/rm -f -- "$tmp"
      echo "Codex archive: $dest appeared during the copy; kept $src" >&2
      return 1
    fi
  fi
  on_archive_volume "$dest" || {
    echo "Codex archive: $dest is no longer on the archive volume; kept $src" >&2
    return 1
  }
  [[ "$(sha256_of "$dest")" == "$want" ]] || {
    echo "Codex archive: archived copy of $src no longer verifies; kept it" >&2
    return 1
  }
  if [[ "$(file_identity "$src")" != "$ident" || "$(sha256_of "$src")" != "$want" ]]; then
    echo "Codex archive: $src changed while being archived; kept it" >&2
    return 1
  fi
  /bin/rm -f -- "$src"
}

archive_old_codex_files() {
  local root rel src count=0 moved=0 status
  local codex_dir=$tidy_home/.codex
  archive_ready || return 0
  # Live sessions/ is deliberately absent from this list.
  for root in "$codex_dir/archived_sessions" "$codex_dir/migration-backups"; do
    require_plain_dir "$root" "Codex archive" || continue
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
    if (( apply )); then
      /usr/bin/find "$root" -mindepth 1 -type d -empty \
        -mmin +$((codex_archive_days * 1440)) -delete 2>/dev/null
    fi
  done
  echo "Codex archive: $count file(s) older than $codex_archive_days days, $moved moved"
}

# Remove a throwaway directory only when a complete traversal finds nothing
# changed inside it within the window (a directory's own mtime ignores nested
# writes). A traversal error is not proof of idleness.
remove_idle_tmp_dir() {
  local dir=$1 days=$2 recent
  [[ "$dir" == "$obd_tmp"/?* && -d "$dir" && ! -L "$dir" ]] || return 1
  if ! recent=$(/usr/bin/find "$dir" -mmin -$((days * 1440)) -print -quit); then
    echo "obd tmp: kept ${dir#"$obd_tmp"/} (could not inspect it completely)" >&2
    failed=1
    return 1
  fi
  if [[ -n "$recent" ]]; then
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
  require_plain_dir "$obd_tmp" "obd tmp" || return 0
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
    [[ -e "$dir" || -L "$dir" ]] || continue
    require_plain_dir "$dir" "cache $label" || continue
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
  require_plain_dir "$agent_shared" "agent-shared" || return 0
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

# Entry used when rootfs_tidy.sh runs this file as the data owner.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  apply=${1:-0}
  failed=0
  run_extra_sweeps
  exit "$failed"
fi
