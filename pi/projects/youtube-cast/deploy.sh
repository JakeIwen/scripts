#!/bin/bash
# Mac-side LAN-only deploy owner; no broad scripts sync participates.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target=pi@vanpi.lan
verb=${1:-install}
case "$verb" in
  install|prepare)
    stage=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$target" \
      'mktemp -d /tmp/youtube-cast-deploy.XXXXXX')
    trap 'ssh -o BatchMode=yes "$target" rm -rf -- "$stage"' EXIT
    scp -q -r "$here/src" "$here/web" "$here/package.json" "$here/package-lock.json" \
      "$here/youtube-cast.service" "$here/controller.sh" "$here/README.md" "$target:$stage/"
    ssh -o BatchMode=yes "$target" /bin/bash "$stage/controller.sh" "$verb"
    ;;
  status|start|stop|restart|verify|uninstall|update-ytdlp)
    ssh -o BatchMode=yes -o ConnectTimeout=10 "$target" \
      /bin/bash /home/pi/youtube-cast/current/controller.sh "$verb"
    ;;
  *) echo 'Usage: deploy.sh {prepare|install|status|start|stop|restart|verify|uninstall|update-ytdlp}' >&2; exit 2 ;;
esac
