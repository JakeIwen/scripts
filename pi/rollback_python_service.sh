#!/usr/bin/env bash
# Owner-run on the Pi (normally piped over SSH); never writes flat Python code.
set -euo pipefail
unit=${1:?usage: rollback_python_service.sh UNIT FLAT_SCRIPT}
flat=${2:?usage: rollback_python_service.sh UNIT FLAT_SCRIPT}
[[ $# -eq 2 ]] || exit 2
case "$unit:$flat" in
  van-dashboard.service:van_dashboard.py|video-library.service:video_library_server.py|audiobooks.service:audiobook_server.py)
    interpreter=/usr/bin/python3 ;;
  bme280-mqtt.service:bme280_mqtt.py)
    interpreter=/home/pi/pyvenv/bin/python ;;
  *) printf 'Unknown service/script pair\n' >&2; exit 2 ;;
esac
root=/home/pi/scripts/python-packages
units=/etc/systemd/system
flat_root=/home/pi/scripts/python-automation
[[ "$(/usr/bin/readlink -e "$root")" == "$root" ]] || exit 1
exec 9>"$root/.install.lock"
/usr/bin/flock -x 9
backup="$root/pre-package-units/$unit.backup"
if [[ "$unit" == van-dashboard.service && ! -e "$backup" ]]; then
  backup="$root/pre-package-units"
fi
[[ "$(/usr/bin/readlink -e "$backup")" == "$backup" ]] || exit 1
[[ -f "$backup/$unit" && ! -L "$backup/$unit" ]] || exit 1
[[ -f "$units/$unit" && ! -L "$units/$unit" ]] || exit 1
test -r "$flat_root/$flat"
grep -Fx "ExecStart=$interpreter $flat_root/$flat" "$backup/$unit"
[[ "$(grep -c '^ExecStart=' "$backup/$unit")" == 1 ]] || exit 1
# Package deployment leaves drop-ins alone. Never overwrite a separate owner's
# newer configuration merely to restore the base unit.
for directory in "$backup/$unit.d" "$units/$unit.d"; do
  [[ ! -L "$directory" ]] || exit 1
done
if [[ -d "$backup/$unit.d" || -d "$units/$unit.d" ]]; then
  if ! diff -rq "$backup/$unit.d" "$units/$unit.d"; then
    printf 'Drop-ins changed; reconcile with their owner before rollback\n' >&2
    exit 1
  fi
fi
umask 077
retired=$(mktemp -d "$root/rollback-state.$unit.XXXXXX")
sudo /usr/bin/install -m 0644 "$backup/$unit" "$units/$unit"
# Retire instead of deleting journals: preserve evidence and block broad sync.
for state in "$root/service-state/$unit.json" "$root/pending-restarts/$unit.json"; do
  if [[ -e "$state" || -L "$state" ]]; then
    parent=${state%/*}
    mkdir -p "$retired/${parent##*/}"
    mv "$state" "$retired/${parent##*/}/"
  fi
done
if [[ "$unit" == van-dashboard.service ]]; then
  for state in "$root/activated.json" "$root/pending-restart"; do
    if [[ -e "$state" || -L "$state" ]]; then mv "$state" "$retired/"; fi
  done
fi
sudo /usr/bin/systemctl daemon-reload
sudo /usr/bin/systemctl restart "$unit"
printf 'Restored %s; retired state: %s\n' "$unit" "$retired"
