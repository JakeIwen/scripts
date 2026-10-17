#!/bin/bash
# Targeted deployment; retains existing local settings and Apple credentials.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
target=${1:-pi@vanpi.lan}
[[ $# -le 1 && "$target" != -* && "$target" != *[[:space:]]* ]] || exit 2
cd "$repo"
python3 -m pi.generate_backup_lock_registry --check
. "$repo/pi/backup_local_deploy.sh"
local_previous=$(local_backup_previous_hashes "$repo" pi)
payload=()
while IFS= read -r source; do payload+=("$source"); done < <(local_backup_sources pi)
PYTHONPATH="$repo:$repo/pi/scripts/backup" python3 -m unittest pi.tests.backup.test_backup_locks pi.tests.backup.test_backup_admission pi.tests.backup.test_protected_json pi.tests.backup.test_local_backup_deploy.DeploymentPlanTests pi.tests.backup.test_local_backup_deploy.DeploymentPreflightTests
python3 "$repo/pi/tests/backup/test_icloud_backup.py"
python3 "$repo/pi/tests/backup/test_icloud_status.py"
bash -n "$repo/pi/scripts/backup/icloud_backup.sh"
stage=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" /usr/bin/mktemp -d /tmp/vanpi-icloud.XXXXXX)
[[ "$stage" =~ ^/tmp/vanpi-icloud\.[[:alnum:]]{6}$ ]] || exit 1
COPYFILE_DISABLE=1 /usr/bin/tar --no-xattrs -cf - pi/backup_local_deploy.sh \
  pi/__init__.py pi/generate_backup_lock_registry.py "${payload[@]}" \
  | ssh -o BatchMode=yes "$target" /bin/tar -xf - -C "$stage"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" /bin/bash -s -- "$stage" "$local_previous" <<'REMOTE'
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
stage=$1
[[ "$stage" =~ ^/tmp/vanpi-icloud\.[[:alnum:]]{6}$ ]] || exit 1
[[ -x /usr/local/bin/rclone ]]
cd "$stage"
/usr/bin/python3 -m pi.generate_backup_lock_registry --check
. pi/backup_local_deploy.sh
while IFS= read -r source; do
  case "$source" in
    *.py) /usr/bin/python3 - "$source" <<'PY'
import ast, sys
from pathlib import Path
ast.parse(Path(sys.argv[1]).read_text())
PY
      ;;
    *.sh) /bin/bash -n "$source" ;;
  esac
done < <(local_backup_sources pi)
/usr/bin/python3 -m json.tool pi/configs/icloud-backup.json >/dev/null
/usr/bin/systemd-analyze verify pi/services/vanpi-icloud-backup*.service pi/services/vanpi-icloud-backup*.timer
sudo -n /usr/bin/python3 pi/scripts/backup/backup_schedule.py --check
local_backup_begin "$stage" pi "$2"
local_backup_publish
/usr/bin/sudo -n /usr/bin/systemd-tmpfiles --create /etc/tmpfiles.d/vanpi-backup.conf
[[ -d /run/lock/vanpi-backup && ! -L /run/lock/vanpi-backup &&
   $(/usr/bin/stat -c '%U %G %a' /run/lock/vanpi-backup) == 'root pi 770' ]] || exit 1
/usr/bin/sudo -n /usr/bin/systemctl daemon-reload
/usr/bin/sudo -n /bin/bash /home/pi/scripts/backup/icloud_backup.sh --preflight
local_backup_commit
sudo -n /usr/bin/python3 /home/pi/scripts/backup/backup_schedule.py --apply
/usr/bin/sudo -n /usr/bin/systemctl enable --now vanpi-icloud-backup.timer vanpi-icloud-backup-resume.timer
/usr/bin/systemctl list-timers --all vanpi-icloud-backup.timer --no-pager
REMOTE
