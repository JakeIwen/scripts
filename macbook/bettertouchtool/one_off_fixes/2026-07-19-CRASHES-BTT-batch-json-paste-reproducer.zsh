#!/bin/zsh

# CRASHES BTT 6.011: BTT crashed while importing the generated
# JSON this placed on the clipboard. Keep this as a reproducer/reference; it is
# not a recommended installation method.

set -euo pipefail

readonly repo_root="${0:A:h:h:h:h}"
readonly json_dir="$repo_root/tmp"
readonly -a item_files=(
  "$json_dir/emoji_floating_submenu.json"
  "$json_dir/addr_floating_submenu.json"
  "$json_dir/sidecar_floating_item.json"
  "$json_dir/spktv_floating_submenu.json"
  "$json_dir/toggle_display_floating_item.json"
  "$json_dir/toggle_night_shift_floating_item.json"
  "$json_dir/recent_notes_floating_submenu.json"
)

command -v jq >/dev/null 2>&1 || { print -u2 -- "ERROR: jq is required"; exit 1; }
command -v pbcopy >/dev/null 2>&1 || { print -u2 -- "ERROR: pbcopy is required"; exit 1; }

for item_file in "${item_files[@]}"; do
  [[ -f "$item_file" ]] || { print -u2 -- "ERROR: missing $item_file"; exit 1; }
done

# Each source contains one complete menu item. Remove only each root item's
# explicit index so BTT appends it; preserve the indexes inside submenus.
jq -s '[.[][] | del(.BTTOrder)]' "${item_files[@]}" | pbcopy

print -- "Copied 7 Media additions to the clipboard. Paste once into Media's item list."
