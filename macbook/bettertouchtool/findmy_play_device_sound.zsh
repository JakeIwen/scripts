#!/bin/zsh

set -euo pipefail

if (( $# != 1 )); then
  print -u2 'usage: findmy_play_device_sound.zsh <exact device title>'
  exit 64
fi

script_dir=${0:A:h}
swift_source="$script_dir/findmy_play_device_sound.swift"
swift_helper="${TMPDIR%/}/findmy_play_device_sound-${UID}"

if [[ ! -x "$swift_helper" || "$swift_source" -nt "$swift_helper" ]]; then
  pending_helper="${swift_helper}.new.${$}"
  /usr/bin/xcrun swiftc -O -o "$pending_helper" "$swift_source"
  /bin/mv -f "$pending_helper" "$swift_helper"
fi

/usr/bin/open -b com.apple.findmy
exec "$swift_helper" "$1"
