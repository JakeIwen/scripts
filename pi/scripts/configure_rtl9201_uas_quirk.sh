#!/bin/bash
# Keep the known-problematic RTL9201 enclosure off UAS. The Linux kernel's
# usb-storage quirk flag "u" binds this one VID:PID to usb-storage instead.
set -euo pipefail

cmdline_path=${RTL9201_UAS_CMDLINE_PATH:-/boot/firmware/cmdline.txt}
backup_path=${RTL9201_UAS_BACKUP_PATH:-${cmdline_path}.pre-rtl9201-uas}
awk_command=${RTL9201_UAS_AWK:-/usr/bin/awk}
cp_command=${RTL9201_UAS_CP:-/bin/cp}
mv_command=${RTL9201_UAS_MV:-/bin/mv}
rm_command=${RTL9201_UAS_RM:-/bin/rm}
sync_command=${RTL9201_UAS_SYNC:-/usr/bin/sync}
quirk_id=0bda:9201
parameter_prefix=usb-storage.quirks=

fail() {
  echo "RTL9201 UAS quirk not installed: $*" >&2
  exit 1
}

if [[ "$cmdline_path" == /boot/firmware/cmdline.txt && ${EUID:-$(id -u)} -ne 0 ]]; then
  fail "run this command with sudo"
fi
for required in "$awk_command" "$cp_command" "$mv_command" "$rm_command" "$sync_command"; do
  [[ -x "$required" ]] || fail "required command is unavailable: $required"
done
[[ -f "$cmdline_path" && ! -L "$cmdline_path" ]] ||
  fail "$cmdline_path is not a regular non-symlink file"

line_count=$("$awk_command" 'NF { count++ } END { print count + 0 }' "$cmdline_path") ||
  fail "cannot inspect $cmdline_path"
[[ "$line_count" == 1 ]] ||
  fail "$cmdline_path must contain exactly one non-empty line (found $line_count)"
cmdline=$("$awk_command" 'NF { print; exit }' "$cmdline_path") ||
  fail "cannot read $cmdline_path"
[[ -n "$cmdline" ]] || fail "$cmdline_path is empty"

read -r -a words <<< "$cmdline"
parameter_count=0
parameter_index=
for index in "${!words[@]}"; do
  if [[ "${words[$index]}" == "$parameter_prefix"* ]]; then
    parameter_count=$((parameter_count + 1))
    parameter_index=$index
  fi
done
(( parameter_count <= 1 )) ||
  fail "$cmdline_path contains more than one $parameter_prefix parameter"

changed=0
if (( parameter_count == 0 )); then
  words+=("${parameter_prefix}${quirk_id}:u")
  changed=1
else
  value=${words[$parameter_index]#"$parameter_prefix"}
  [[ -n "$value" ]] || fail "the existing $parameter_prefix parameter is empty"
  IFS=',' read -r -a entries <<< "$value"
  target_count=0
  for index in "${!entries[@]}"; do
    entry=${entries[$index]}
    if [[ "$entry" == "$quirk_id:"* ]]; then
      target_count=$((target_count + 1))
      flags=${entry#"$quirk_id:"}
      [[ "$flags" =~ ^[a-zA-Z]*$ ]] ||
        fail "invalid flags in existing quirk entry '$entry'"
      if [[ "$flags" != *u* ]]; then
        entries[$index]="${quirk_id}:${flags}u"
        changed=1
      fi
    fi
  done
  (( target_count <= 1 )) ||
    fail "$cmdline_path contains duplicate $quirk_id quirk entries"
  if (( target_count == 0 )); then
    entries+=("${quirk_id}:u")
    changed=1
  fi
  joined=$(IFS=,; printf '%s' "${entries[*]}")
  words[$parameter_index]="${parameter_prefix}${joined}"
fi

if (( ! changed )); then
  echo "RTL9201 already has the kernel IGNORE_UAS quirk; no change needed"
  exit 0
fi

new_cmdline=$(printf '%s ' "${words[@]}")
new_cmdline=${new_cmdline% }
[[ "$new_cmdline" =~ (^|=|,)${quirk_id}:[a-zA-Z]*u[a-zA-Z]*($|,|[[:space:]]) ]] ||
  fail "internal validation did not produce the expected quirk"

if [[ -e "$backup_path" || -L "$backup_path" ]]; then
  [[ -f "$backup_path" && ! -L "$backup_path" ]] ||
    fail "refusing unsafe backup path $backup_path"
else
  "$cp_command" -p -- "$cmdline_path" "$backup_path" ||
    fail "cannot create one-time backup $backup_path"
fi

temporary="${cmdline_path}.rtl9201.$$"
[[ ! -e "$temporary" && ! -L "$temporary" ]] ||
  fail "temporary path already exists: $temporary"
cleanup() {
  "$rm_command" -f -- "$temporary"
}
trap cleanup EXIT

"$cp_command" -p -- "$cmdline_path" "$temporary" ||
  fail "cannot stage the updated kernel command line"
printf '%s\n' "$new_cmdline" > "$temporary" ||
  fail "cannot write the staged kernel command line"
"$sync_command" -f "$temporary" ||
  fail "cannot sync the staged kernel command line"
"$mv_command" -f -- "$temporary" "$cmdline_path" ||
  fail "cannot install the updated kernel command line"
trap - EXIT

echo "installed ${quirk_id}:u in $cmdline_path; reboot required"
