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

if [[ "$task" == ensure_rear_movie ]]; then
  # A healthy check changes nothing, including ALSA. exec lets the caller's
  # timeout stop Python itself instead of leaving an orphaned repair behind.
  exec python3 -c 'from sonos_tasks import ensure_rear_movie; ensure_rear_movie()'
fi

case "$#" in
  "1")
    args="";;
  "2")
    args="'$2'";; 
  "3")
    args="'$2', '$3'";;
  "4")
    args="'$2', '$3', '$4'";;
esac

py_cmd="from sonos_tasks import $task; $task($args)"

echo "setting system volume to 90%"
amixer cset numid=1 90%

echo  "running python3: $py_cmd"
python3 -c "$py_cmd"

