#!/bin/zsh

set -euo pipefail

script_dir=${0:A:h}
click_source="$script_dir/findmy_mouse_click.swift"
click_helper="${TMPDIR%/}/findmy_mouse_click-${UID}"

if [[ ! -x "$click_helper" || "$click_source" -nt "$click_helper" ]]; then
  pending_helper="${click_helper}.new.${$}"
  /usr/bin/xcrun swiftc -O -o "$pending_helper" "$click_source"
  /bin/mv -f "$pending_helper" "$click_helper"
fi

export FINDMY_CLICK_HELPER="$click_helper"
exec /usr/bin/osascript "$script_dir/findmy_play_sound.applescript" "$@"
