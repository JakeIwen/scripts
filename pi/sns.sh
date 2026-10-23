#! /bin/bash
export PYTHONDONTWRITEBYTECODE=1
package_root=/home/pi/scripts/python-packages/current
package_sonos_path="$package_root/shared/python"
flat_sonos_path=/home/pi/scripts/python-automation
case ":${PYTHONPATH:-}:" in
  *":$package_sonos_path:"*|*":$flat_sonos_path:"*)
    # Keep an explicit package or frozen flat video environment first.  The
    # latter is the valid Sonos source during a pre-package rollback.
    ;;
  *)
    package_paths="$package_root:$package_sonos_path:$package_root/pi/scripts/python"
    if [[ -n "${PYTHONPATH:-}" ]]; then
      PYTHONPATH="$PYTHONPATH:$package_paths"
    else
      PYTHONPATH="$package_paths"
    fi
    export PYTHONPATH
    ;;
esac

task=$1

if [[ "$task" == ensure_rear_movie && "$#" == 1 ]]; then
  # A healthy check changes nothing, including ALSA. exec lets the caller's
  # timeout stop Python itself instead of leaving an orphaned repair behind.
  exec python3 -c 'from sonos_tasks import ensure_rear_movie; ensure_rear_movie()'
fi

# The CLI is shipped in package releases. Keep a pinned/rollback task module
# first, adding current only when it is needed to find the CLI itself.
case ":${PYTHONPATH:-}:" in
  *":$package_sonos_path:"*) ;;
  *) export PYTHONPATH="$PYTHONPATH:$package_sonos_path" ;;
esac
exec python3 -m sonos_cli "$@"
