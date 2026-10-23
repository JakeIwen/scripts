#!/bin/bash
# Permanent entry point for the shell alias and BetterTouchTool actions.
sns_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
sns_repo_root="$(cd -- "$sns_script_dir/../.." && pwd)" || exit 1

if [[ "${1:-}" == --pi ]]; then
  shift
  # SSH runs a remote command string: quote every argument for the Pi's Bash.
  printf -v sns_remote_args ' %q' "$@"
  exec ssh -o BatchMode=yes -o ConnectTimeout=5 vanpi \
    "/bin/bash /home/pi/sns.sh$sns_remote_args" 2>&1
fi
[[ "${1:-}" != --local ]] || shift

sns_python="${SNS_PYTHON:-$HOME/py3env/bin/python}"
if [[ ! -x "$sns_python" ]]; then
  echo "sns: Python not found at $sns_python; set SNS_PYTHON to a Python with SoCo installed." >&2
  exit 1
fi
export PYTHONPATH="$sns_repo_root/shared/python${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
# Errors stay visible in BTT. Never replay an uncertain operation on another host.
exec "$sns_python" -u -m sonos_cli "$@" 2>&1
