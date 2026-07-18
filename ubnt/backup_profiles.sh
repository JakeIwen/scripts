#!/bin/bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
target=${1:-ubnt@192.168.8.20}
sync_working=${2:-}
backup_stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_root="$script_dir/private-backups/$backup_stamp"
staging_root=$(mktemp -d /tmp/ubnt-profile-backup.XXXXXX)

cleanup() {
    rm -rf "$staging_root"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$backup_root/local-before-sync" "$backup_root/live"
chmod 700 "$backup_root"

if compgen -G "$script_dir/persistent/profiles/*" >/dev/null; then
    cp -pR "$script_dir/persistent/profiles/." "$backup_root/local-before-sync/"
fi

scp -q -r -O -o BatchMode=yes -o ConnectTimeout=5 \
    "$target:/etc/persistent/profiles" "$staging_root/"

profile_count=$(find "$staging_root/profiles" -maxdepth 1 -type f | wc -l | tr -d ' ')
if [[ "$profile_count" -lt 1 ]]; then
    echo 'Refusing to continue: live profile backup is empty.' >&2
    exit 1
fi

while IFS= read -r profile; do
    if ! grep -q '^wireless\.1\.ssid=' "$profile"; then
        echo "Refusing to continue: malformed profile ${profile##*/}." >&2
        exit 1
    fi
done < <(find "$staging_root/profiles" -maxdepth 1 -type f -print)

cp -pR "$staging_root/profiles/." "$backup_root/live/"
chmod -R go-rwx "$backup_root"

while IFS= read -r staged_profile; do
    profile_name=${staged_profile##*/}
    staged_hash=$(md5 -q "$staged_profile")
    backup_hash=$(md5 -q "$backup_root/live/$profile_name")
    if [[ "$staged_hash" != "$backup_hash" ]]; then
        echo "Backup verification failed for $profile_name." >&2
        exit 1
    fi
done < <(find "$staging_root/profiles" -maxdepth 1 -type f -print)

if [[ "$sync_working" == --sync-working ]]; then
    mkdir -p "$script_dir/persistent/profiles"
    cp -p "$staging_root/profiles/"* "$script_dir/persistent/profiles/"
fi

printf 'Verified %s live profiles in %s\n' "$profile_count" "$backup_root"
