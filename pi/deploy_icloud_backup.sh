#!/bin/bash
# Targeted deployment; retains existing local settings and Apple credentials.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
target=${1:-pi@vanpi.lan}
[[ $# -le 1 ]] || exit 2
python3 "$repo/pi/tests/backup/test_icloud_backup.py"
bash -n "$repo/pi/scripts/backup/icloud_backup.sh"
stage=$(ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" /usr/bin/mktemp -d /tmp/vanpi-icloud.XXXXXX)
[[ "$stage" =~ ^/tmp/vanpi-icloud\.[[:alnum:]]{6}$ ]] || exit 1
scp -q "$repo/pi/scripts/backup/icloud_backup.py" \
  "$repo/pi/scripts/backup/icloud_uplink.py" \
  "$repo/pi/scripts/backup/icloud_backup.sh" \
  "$repo/pi/scripts/backup/ICLOUD_RESTORE.txt" \
  "$repo/pi/configs/icloud-backup.json" \
  "$repo/pi/services/vanpi-icloud-backup.service" \
  "$repo/pi/services/vanpi-icloud-backup.timer" "$target:$stage/"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$target" /bin/bash -s -- "$stage" <<'REMOTE'
set -euo pipefail
stage=$1
[[ "$stage" =~ ^/tmp/vanpi-icloud\.[[:alnum:]]{6}$ ]] || exit 1
[[ -x /usr/local/bin/rclone ]]
/usr/bin/python3 -m py_compile "$stage/icloud_backup.py" "$stage/icloud_uplink.py"
/bin/bash -n "$stage/icloud_backup.sh"
/usr/bin/python3 -m json.tool "$stage/icloud-backup.json" >/dev/null
/usr/bin/systemd-analyze verify "$stage/vanpi-icloud-backup.service" "$stage/vanpi-icloud-backup.timer"
# A running job must finish normally before updating its executable code.
if /usr/bin/systemctl is-active --quiet vanpi-icloud-backup.service; then
  echo 'iCloud backup is active; defer deployment until it finishes.' >&2
  exit 1
fi
/usr/bin/install -d -m 0700 "$stage/previous"
for name in icloud_backup.py icloud_uplink.py icloud_backup.sh ICLOUD_RESTORE.txt; do
  live=/home/pi/scripts/backup/$name
  [[ ! -e "$live" ]] || /usr/bin/cp -p "$live" "$stage/previous/$name"
  /usr/bin/sudo -n /usr/bin/install -o pi -g pi -m 0750 "$stage/$name" "$live"
done
if [[ ! -e /etc/vanpi-icloud-backup.json ]]; then
  /usr/bin/sudo -n /usr/bin/install -o root -g root -m 0600 "$stage/icloud-backup.json" /etc/vanpi-icloud-backup.json
fi
for name in vanpi-icloud-backup.service vanpi-icloud-backup.timer; do
  [[ ! -e /etc/systemd/system/$name ]] || /usr/bin/cp -p /etc/systemd/system/"$name" "$stage/previous/$name"
  /usr/bin/sudo -n /usr/bin/install -o root -g root -m 0644 "$stage/$name" /etc/systemd/system/"$name"
done
/usr/bin/sudo -n /usr/bin/systemctl daemon-reload
/usr/bin/sudo -n /bin/bash /home/pi/scripts/backup/icloud_backup.sh --preflight
/usr/bin/sudo -n /usr/bin/systemctl enable --now vanpi-icloud-backup.timer
/usr/bin/systemctl list-timers --all vanpi-icloud-backup.timer --no-pager
echo "Deployment rollback files: $stage/previous"
REMOTE
