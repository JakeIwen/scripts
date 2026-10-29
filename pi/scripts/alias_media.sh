#!/bin/bash
# Permanent am/an and qBittorrent compatibility entry; implementation belongs
# to pi.apps.media_organizer.alias_main in the immutable Python package release.
export PYTHONDONTWRITEBYTECODE=1
package_root=/home/pi/scripts/python-packages/current
case ":${PYTHONPATH:-}:" in
  *":$package_root:"*) ;;
  *)
    if [ -n "${PYTHONPATH:-}" ]; then
      export PYTHONPATH="$PYTHONPATH:$package_root"
    else
      export PYTHONPATH="$package_root"
    fi
    ;;
esac
exec python3 -P -m pi.apps.media_organizer.alias_main "$@"
