#!/bin/zsh
# Noninteractive: preserves the held modifiers and captures before any UI changes.
set -euo pipefail
umask 077

if [[ ${1:-} == --help ]]; then
  print 'Capture all displays to repo tmp/btt-screenshots and copy the first PNG to the clipboard.'
  exit 0
fi
if (( $# )); then
  print -u2 'Usage: capture_btt_menu.zsh [--help]'
  exit 64
fi

capture_root="${0:A:h:h:h}/tmp/btt-screenshots"
/bin/mkdir -p "$capture_root"
capture_dir=$(/usr/bin/mktemp -d "$capture_root/capture-XXXXXX")
capture_file="$capture_dir/screen.png"
/usr/sbin/screencapture -x -C -T 0 "$capture_file"
[[ -s "$capture_file" ]] || { print -u2 'Screen capture produced no image.'; exit 1; }

# Clipboard work happens only after the screenshot has captured the floating menu.
if ! /usr/bin/osascript - "$capture_file" <<'APPLESCRIPT'
on run argv
  set the clipboard to (read (POSIX file (item 1 of argv)) as «class PNGf»)
end run
APPLESCRIPT
then
  print -u2 "Screenshot saved, but clipboard copy failed: $capture_file"
fi
print "Saved screenshot: $capture_file"
