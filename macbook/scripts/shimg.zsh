#!/bin/zsh
# Save a clipboard image through the mounted Pi share without changing the clipboard.
set -euo pipefail

if [[ ${1:-} == --help ]]; then
  print 'Usage: shimg [filename.png]'
  print 'Save the clipboard image to the Pi agent-shared folder (default: screenshot.png).'
  print 'A missing .png suffix is added. An existing file is replaced.'
  print 'An unmounted pihome share is mounted and its folder shown in Finder.'
  exit 0
fi
if (( $# > 1 )) || [[ ${1-screenshot} == '' || ${1:-} == */* ]]; then
  print -u2 'Usage: shimg [filename.png] (a filename, without directories)'
  exit 64
fi

filename=${1:-screenshot.png}
[[ $filename == *.[pP][nN][gG] ]] || filename+=.png
share=/Volumes/pihome/Documents/agent-shared
share_url=smb://pi@VANPI._smb._tcp.local/pihome
if [[ ! -d $share ]]; then
  # Mount quietly, then show the folder; open focuses a Finder window already on it.
  /usr/bin/osascript -e "mount volume \"$share_url\"" >/dev/null 2>&1 || true
  if [[ ! -d $share ]]; then
    print -u2 "Pi share unavailable: $share. Mounting pihome failed (van LAN only)."
    exit 1
  fi
  /usr/bin/open "$share"
fi
if [[ -d $share/$filename ]]; then
  print -u2 "Destination is a directory: $share/$filename"
  exit 1
fi

# Stage beside the destination so failed clipboard reads never truncate an existing image.
staging=$(/usr/bin/mktemp -d "$share/.shimg-XXXXXX")
trap '/bin/rm -rf -- "$staging"' EXIT
/usr/bin/osascript - "$staging/image.png" <<'APPLESCRIPT'
on run argv
  set imageData to the clipboard as «class PNGf»
  set output to open for access (POSIX file (item 1 of argv)) with write permission
  try
    write imageData to output
    close access output
  on error messageText number errorNumber
    close access output
    error messageText number errorNumber
  end try
end run
APPLESCRIPT
/bin/mv -f -- "$staging/image.png" "$share/$filename"
print -r -- "$share/$filename"
