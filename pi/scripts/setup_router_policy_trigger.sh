#!/bin/bash
set -euo pipefail

TRIGGER_USER=router-trigger
TRIGGER_GROUP=router-trigger
TRIGGER_HOME=/var/lib/router-trigger
ROUTER_SOURCE_IP=192.168.6.1
POLICY_UNIT=vanpi-policy.service
AUTHORIZED_KEYS="$TRIGGER_HOME/.ssh/authorized_keys"
SUDOERS_FILE=/etc/sudoers.d/router-trigger-vanpi-policy

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

if (( EUID != 0 )); then
  fail "run this setup through sudo and provide the router public key on stdin"
fi

for required_path in \
  /usr/bin/chmod \
  /usr/bin/chown \
  /usr/bin/cut \
  /usr/bin/getent \
  /usr/bin/install \
  /usr/bin/mktemp \
  /usr/bin/mv \
  /usr/bin/rm \
  /usr/bin/sudo \
  /usr/bin/systemctl \
  /usr/sbin/useradd \
  /usr/sbin/usermod \
  /usr/sbin/visudo
do
  [[ -x "$required_path" ]] || fail "required command is missing: $required_path"
done

IFS= read -r public_key || fail "no public key received on stdin"
[[ -n "$public_key" ]] || fail "empty public key received on stdin"
if IFS= read -r extra_line; then
  fail "expected exactly one public-key line"
fi

read -r key_type key_blob _ <<< "$public_key"
[[ "$key_type" == "ssh-ed25519" ]] || fail "only an Ed25519 trigger key is accepted"
[[ "$key_blob" =~ ^[A-Za-z0-9+/]+={0,3}$ ]] || fail "malformed public-key body"

if ! /usr/bin/getent passwd "$TRIGGER_USER" >/dev/null; then
  /usr/sbin/useradd \
    --system \
    --user-group \
    --home-dir "$TRIGGER_HOME" \
    --create-home \
    --shell /bin/sh \
    "$TRIGGER_USER"
fi

account_entry="$(/usr/bin/getent passwd "$TRIGGER_USER")"
account_home="$(/usr/bin/cut -d: -f6 <<< "$account_entry")"
account_shell="$(/usr/bin/cut -d: -f7 <<< "$account_entry")"
[[ "$account_home" == "$TRIGGER_HOME" ]] \
  || fail "$TRIGGER_USER has unexpected home directory: $account_home"
[[ "$account_shell" == "/bin/sh" ]] \
  || fail "$TRIGGER_USER has unexpected shell: $account_shell"

/usr/sbin/usermod --lock "$TRIGGER_USER"
/usr/bin/install -d -m 0750 -o "$TRIGGER_USER" -g "$TRIGGER_GROUP" "$TRIGGER_HOME"
/usr/bin/install -d -m 0700 -o "$TRIGGER_USER" -g "$TRIGGER_GROUP" "$TRIGGER_HOME/.ssh"

auth_tmp=""
sudoers_tmp=""
cleanup() {
  /usr/bin/rm -f -- "${auth_tmp:-}" "${sudoers_tmp:-}"
}
trap cleanup EXIT

auth_tmp="$(/usr/bin/mktemp "$TRIGGER_HOME/.ssh/.authorized_keys.XXXXXX")"
sudoers_tmp="$(/usr/bin/mktemp /etc/sudoers.d/.router-trigger-vanpi-policy.XXXXXX)"
printf '%s %s %s %s\n' \
  "from=\"$ROUTER_SOURCE_IP\",restrict,command=\"/usr/bin/sudo -n /usr/bin/systemctl start --no-block $POLICY_UNIT\"" \
  "$key_type" \
  "$key_blob" \
  mwan3-policy-trigger > "$auth_tmp"
/usr/bin/chown "$TRIGGER_USER:$TRIGGER_GROUP" "$auth_tmp"
/usr/bin/chmod 0600 "$auth_tmp"

printf '%s\n' \
  "$TRIGGER_USER ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block $POLICY_UNIT" \
  > "$sudoers_tmp"
/usr/bin/chown root:root "$sudoers_tmp"
/usr/bin/chmod 0440 "$sudoers_tmp"
/usr/sbin/visudo -cf "$sudoers_tmp" >/dev/null
/usr/bin/mv -f -- "$sudoers_tmp" "$SUDOERS_FILE"
sudoers_tmp=""
/usr/bin/mv -f -- "$auth_tmp" "$AUTHORIZED_KEYS"
auth_tmp=""

echo "installed restricted $TRIGGER_USER access for $POLICY_UNIT"
