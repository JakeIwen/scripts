#!/usr/bin/env bash
set -euo pipefail

if [[ $# -eq 0 ]]; then
  echo "Usage: vanpi-send <file> [file2 ...]"
  exit 1
fi

DEST="pi@vanpi:~/claude/shared-files"

for src in "$@"; do
  # Strip any surrounding quotes macOS drag-and-drop sometimes adds
  src="${src%\'}"
  src="${src#\'}"
  src="${src%\"}"
  src="${src#\"}"

  if [[ ! -f "$src" ]]; then
    echo "Error: not a file: $src" >&2
    continue
  fi

  filename="$(basename "$src")"
  echo "Sending $filename ..."
  scp "$src" "$DEST/$filename"
  echo "Done → $DEST/$filename"
done
