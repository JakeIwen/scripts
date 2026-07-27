#!/bin/sh
set -u

key_source=${UBNT_SSH_KEY_SOURCE:-/etc/persistent/config/raspi_rsa_id.pub}
authorized_keys=${UBNT_AUTHORIZED_KEYS:-/etc/dropbear/authorized_keys}

[ -s "$key_source" ] || exit 0

touch "$authorized_keys" || exit 1
while IFS= read -r key; do
    [ -n "$key" ] || continue
    if ! grep -Fqx "$key" "$authorized_keys" 2>/dev/null; then
        printf '%s\n' "$key" >> "$authorized_keys" || exit 1
    fi
done < "$key_source"

sort -u "$authorized_keys" > "$authorized_keys.new" || exit 1
mv "$authorized_keys.new" "$authorized_keys" || exit 1
chmod 600 "$authorized_keys"
