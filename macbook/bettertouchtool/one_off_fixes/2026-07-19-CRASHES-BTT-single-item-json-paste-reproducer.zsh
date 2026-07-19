#!/bin/zsh

# CRASHES BTT 6.011: BTT crashed while importing the generated
# JSON this placed on the clipboard. Keep this as a reproducer/reference; it is
# not a recommended installation method.

set -euo pipefail

readonly repo_root="${0:A:h:h:h:h}"
readonly json_dir="$repo_root/tmp"
readonly item_name="${1:-}"

case "$item_name" in
  emoji)         item_file="$json_dir/emoji_floating_submenu.json" ;;
  addr)          item_file="$json_dir/addr_floating_submenu.json" ;;
  sidecar)       item_file="$json_dir/sidecar_floating_item.json" ;;
  spktv)         item_file="$json_dir/spktv_floating_submenu.json" ;;
  display-power) item_file="$json_dir/toggle_display_floating_item.json" ;;
  night-shift)   item_file="$json_dir/toggle_night_shift_floating_item.json" ;;
  recent-notes)  item_file="$json_dir/recent_notes_floating_submenu.json" ;;
  *)
    print -u2 -- "Usage: ${0:t} {emoji|addr|sidecar|spktv|display-power|night-shift|recent-notes}"
    exit 2
    ;;
esac

command -v jq >/dev/null 2>&1 || { print -u2 -- "ERROR: jq is required"; exit 1; }
command -v pbcopy >/dev/null 2>&1 || { print -u2 -- "ERROR: pbcopy is required"; exit 1; }
[[ -f "$item_file" ]] || { print -u2 -- "ERROR: missing $item_file"; exit 1; }

# A single root item per paste is reliable in BTT. Remove only its root index so
# it is appended; keep all indexes inside a submenu.
jq 'map(del(.BTTOrder))' "$item_file" | pbcopy
print -- "Copied $item_name. Paste once into Media's item list, then wait for BTT to finish."
