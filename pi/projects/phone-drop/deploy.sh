#!/bin/bash
# Install or update Phone Drop on the Pi from this checkout. Idempotent: creates
# the inbox and a private token only when missing, restarts only on change, and
# never prints the token. Run from the Mac: bash pi/projects/phone-drop/deploy.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="${PHONE_DROP_TARGET:-pi@vanpi.lan}"

stage="$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$target" \
  'mktemp -d /tmp/phone-drop-deploy.XXXXXX')"
scp -q "$here/phone_drop.py" "$here/phone-drop.service" "$target:$stage/"

ssh -o BatchMode=yes "$target" /bin/bash -s -- "$stage" <<'REMOTE'
set -euo pipefail
stage=$1
case "$stage" in /tmp/phone-drop-deploy.*) ;; *) echo "bad stage $stage" >&2; exit 1 ;; esac
trap 'rm -rf -- "$stage"' EXIT

app_dir=/home/pi/phone-drop
inbox=/home/pi/Documents/agent-shared/from-phone
token_dir=/home/pi/.config/phone-drop
unit=phone-drop.service
live_unit=/etc/systemd/system/$unit
port=8793

/usr/bin/python3 -c 'import werkzeug' ||
  { echo "python3-werkzeug is missing on the Pi" >&2; exit 1; }
/usr/bin/python3 -B -m py_compile "$stage/phone_drop.py"

changed=0
install -d -m 0755 "$app_dir" "$inbox"
if ! cmp -s "$stage/phone_drop.py" "$app_dir/phone_drop.py"; then
  install -m 0644 "$stage/phone_drop.py" "$app_dir/phone_drop.py.new"
  mv -f -- "$app_dir/phone_drop.py.new" "$app_dir/phone_drop.py"
  changed=1
fi

install -d -m 0700 "$token_dir"
if [[ ! -s "$token_dir/token" ]]; then
  (umask 077
   /usr/bin/python3 -c 'import secrets; print(secrets.token_urlsafe(32))' \
     > "$token_dir/token.new"
   mv -f -- "$token_dir/token.new" "$token_dir/token")
  echo "created a new upload token in $token_dir/token"
  changed=1
fi
chmod 0600 "$token_dir/token"

if ! cmp -s "$stage/$unit" "$live_unit"; then
  sudo install -m 0644 -o root -g root "$stage/$unit" "$live_unit"
  sudo systemctl daemon-reload
  changed=1
fi
sudo systemctl enable --quiet "$unit"
if (( changed )) || ! systemctl is-active --quiet "$unit"; then
  sudo systemctl restart "$unit"
fi

for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then break; fi
  sleep 0.5
done
curl -fsS "http://127.0.0.1:$port/health" >/dev/null ||
  { systemctl --no-pager status "$unit" >&2 || true; echo "health check failed" >&2; exit 1; }
status="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
  -H 'Authorization: Bearer not-the-token' "http://127.0.0.1:$port/upload")"
[[ "$status" == 401 ]] ||
  { echo "expected 401 for a wrong token, got $status" >&2; exit 1; }
echo "phone-drop is running: health ok, wrong token refused, inbox $inbox"
REMOTE
