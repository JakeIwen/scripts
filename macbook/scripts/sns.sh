#! /bin/bash

# GUI launchers such as BTT don't load .zshrc. Resolve this checkout without
# relying on the caller's working directory or inherited PYTHONPATH.
sns_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
sns_repo_root="$(cd -- "$sns_script_dir/../.." && pwd)" || exit 1
export PYTHONPATH="$sns_repo_root/shared/python${PYTHONPATH:+:$PYTHONPATH}"

source "/Users/jacobr/py3env/bin/activate" || exit 1
task=$1

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
echo  "running python3: $py_cmd"
# BTT's result pane may show stdout only; expose errors and don't buffer task
# progress while discovery/network requests are running. Keep the old fallback.
python -u -c "$py_cmd" 2>&1 || rpi "python3 -c '$py_cmd'" 2>&1
