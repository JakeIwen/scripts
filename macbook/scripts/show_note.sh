#!/bin/bash

set -euo pipefail

if (( $# == 0 )); then
  echo "usage: ${0##*/} NOTE_TITLE" >&2
  echo "       ${0##*/} --id NOTE_ID" >&2
  exit 64
fi

if [[ $1 == "--id" ]]; then
  if (( $# != 2 )); then
    echo "usage: ${0##*/} --id NOTE_ID" >&2
    exit 64
  fi
  lookup_mode="id"
  lookup_value=$2
else
  # Join multiple arguments for convenience, while still allowing callers to
  # preserve exact whitespace by passing the title as one quoted argument.
  lookup_mode="title"
  lookup_value="$*"
fi

# Pass the title through argv rather than interpolating it into AppleScript.
# This keeps quotes and other shell/AppleScript metacharacters in note titles
# from changing the program being executed.
/usr/bin/osascript - "$lookup_mode" "$lookup_value" <<'APPLESCRIPT'
on run argv
  set lookupMode to item 1 of argv
  set lookupValue to item 2 of argv

  tell application "/System/Applications/Notes.app"
    if lookupMode is "id" then
      set matchingNotes to every note whose id is lookupValue
    else
      set matchingNotes to every note whose name is lookupValue
    end if

    if (count of matchingNotes) is 0 then
      error "Note not found: " & lookupValue number 2
    end if

    -- Title lookup can return duplicates. ID lookup normally returns one note.
    set chosenNote to item 1 of matchingNotes
    set chosenDate to modification date of chosenNote
    repeat with candidateNote in matchingNotes
      set candidateDate to modification date of candidateNote
      if candidateDate > chosenDate then
        set chosenNote to contents of candidateNote
        set chosenDate to candidateDate
      end if
    end repeat

    activate
    show chosenNote
  end tell
end run
APPLESCRIPT
