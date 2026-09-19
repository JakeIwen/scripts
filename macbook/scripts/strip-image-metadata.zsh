#!/bin/zsh
# BTT entry point; with file arguments this can also be used from Terminal.
set -eu
script_dir=${0:A:h}
app="$script_dir/../build/Strip Metadata.app"
if [[ ! -x "$app/Contents/MacOS/StripMetadata" ]]; then
  /usr/bin/python3 "$script_dir/build_strip_metadata_app.py"
fi
if (( $# )); then
  exec "$app/Contents/MacOS/StripMetadata" "$@"
fi
exec /usr/bin/open "$app"
