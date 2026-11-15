# shellcheck shell=bash
# Age-based SD-root sweeps for rootfs_tidy.sh (it owns the dry-run/--apply
# contract). As root, rootfs_tidy.sh runs this file as the data owner
# (`bash rootfs_tidy_sweeps.sh <apply 0|1>` under setpriv), so a symlink swap
# or racing writer can only reach that user's own files. The Codex session
# delete is rootfs_tidy_codex_sessions.py beside this file: re-checking each
# file's identity right before unlinkat needs descriptor-relative no-follow
# operations shell lacks.
# Every failure sets failed=1, keeps the source, and the run exits nonzero.
#
# Roots and tools are overridable so tests run against a fixture tree.
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
tidy_home=${ROOTFS_TIDY_HOME:-/home/pi}
codex_python=${ROOTFS_TIDY_CODEX_PYTHON:-/usr/bin/python3}
codex_tool=${ROOTFS_TIDY_CODEX_TOOL:-$script_dir/rootfs_tidy_codex_sessions.py}
suite_days=${ROOTFS_TIDY_SUITE_DAYS:-2}
playwright_days=${ROOTFS_TIDY_PLAYWRIGHT_DAYS:-14}
cache_days=${ROOTFS_TIDY_CACHE_DAYS:-30}
big_file_bytes=${ROOTFS_TIDY_BIG_BYTES:-209715200}
big_file_days=${ROOTFS_TIDY_BIG_DAYS:-14}

physical_path() { (cd -P -- "$1" 2>/dev/null && pwd -P); }

# Resolve the home once; every default root hangs off the physical path.
tidy_home=$(physical_path "$tidy_home") || tidy_home=/nonexistent-tidy-home
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

# Codex archived sessions older than ROOTFS_TIDY_CODEX_DAYS (default 30); the
# module's docstring has the race checks.
delete_old_codex_sessions() {
  local mode=--dry-run
  (( apply )) && mode=--apply
  "$codex_python" -I "$codex_tool" "$mode" --home "$tidy_home" || failed=1
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
  delete_old_codex_sessions
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
