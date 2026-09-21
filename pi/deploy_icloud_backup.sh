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
  "$repo/pi/scripts/backup/icloud_progress.py" \
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
/usr/bin/python3 -m py_compile "$stage/icloud_backup.py" "$stage/icloud_uplink.py" "$stage/icloud_progress.py"
/bin/bash -n "$stage/icloud_backup.sh"
/usr/bin/python3 -m json.tool "$stage/icloud-backup.json" >/dev/null
/usr/bin/systemd-analyze verify "$stage/vanpi-icloud-backup.service" "$stage/vanpi-icloud-backup.timer"
# A running job must finish normally before updating its executable code.
service_state=$(/usr/bin/systemctl show vanpi-icloud-backup.service --property=ActiveState --value)
case "$service_state" in
  inactive|failed) ;;
  *) echo 'iCloud backup may be active; defer deployment until it stops.' >&2; exit 1 ;;
esac
/usr/bin/install -d -m 0700 "$stage/previous"
for name in icloud_backup.py icloud_progress.py icloud_uplink.py icloud_backup.sh ICLOUD_RESTORE.txt; do
  live=/home/pi/scripts/backup/$name
  [[ ! -e "$live" ]] || /usr/bin/cp -p "$live" "$stage/previous/$name"
  /usr/bin/sudo -n /usr/bin/install -o pi -g pi -m 0750 "$stage/$name" "$live"
done
if [[ ! -e /etc/vanpi-icloud-backup.json ]]; then
  /usr/bin/sudo -n /usr/bin/install -o root -g root -m 0600 "$stage/icloud-backup.json" /etc/vanpi-icloud-backup.json
fi
# Add newly introduced defaults without overwriting any existing preference.
/usr/bin/sudo -n /usr/bin/python3 - "$stage/icloud-backup.json" <<'PY'
import json,os,stat,sys,tempfile
from pathlib import Path
p=Path('/etc/vanpi-icloud-backup.json');s=p.lstat()
assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022
old=p.read_bytes();current=json.loads(old);defaults=json.loads(Path(sys.argv[1]).read_text())
updated=dict(defaults,**current)
if updated != current:
    fd,tmp=tempfile.mkstemp(prefix='.icloud-config-',dir=p.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(updated,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        assert p.read_bytes()==old,'Settings changed concurrently'
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
PY
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
