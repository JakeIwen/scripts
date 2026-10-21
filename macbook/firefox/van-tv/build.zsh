#!/bin/zsh
set -eu -o pipefail
cd "${0:A:h}"
mkdir -p dist
staging=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/van-tv-build.XXXXXX")
trap '/bin/rm -f "$staging/van-tv.xpi"; /bin/rmdir "$staging"' EXIT
/usr/bin/zip -X -q "$staging/van-tv.xpi" \
  manifest.json background.html popup.html *.mjs config.js content.js *.css icon.svg
/bin/mv -f "$staging/van-tv.xpi" dist/van-tv.xpi
printf '%s\n' "$PWD/dist/van-tv.xpi"
