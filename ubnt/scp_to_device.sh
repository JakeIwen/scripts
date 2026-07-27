#!/bin/bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
mode=${1:-}
target=${2:-ubnt@192.168.8.20}

case $mode in
    --stage-only|--install-paused|--activate) ;;
    *)
        echo "Usage: $0 --stage-only|--install-paused|--activate [user@host]" >&2
        exit 1
        ;;
esac

"$script_dir/backup_profiles.sh" "$target" --sync-working

stage_root=$(mktemp -d /tmp/ubnt-code-stage.XXXXXX)
remote_stage="/tmp/ubnt-code-stage.$$"

cleanup() {
    rm -rf "$stage_root"
    ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" "rm -rf '$remote_stage'" >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$stage_root/persistent/config" "$stage_root/persistent/scripts"
cp -p "$script_dir/persistent/rc.postsysinit" "$stage_root/persistent/"
cp -p "$script_dir/persistent/config/cron" \
    "$script_dir/persistent/config/.profile" \
    "$script_dir/persistent/config/wifi-priority" \
    "$script_dir/persistent/config/raspi_rsa_id.pub" \
    "$stage_root/persistent/config/"
cp -p "$script_dir/persistent/scripts/"*.sh \
    "$script_dir/persistent/scripts/"*.awk \
    "$stage_root/persistent/scripts/"

while IFS= read -r script; do
    /bin/sh -n "$script"
done < <(find "$stage_root/persistent" -type f \( -name '*.sh' -o -name rc.postsysinit \) -print)

ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" "mkdir -p '$remote_stage'"
scp -q -r -O -o BatchMode=yes -o ConnectTimeout=5 \
    "$stage_root/persistent" "$target:$remote_stage/"

ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" "
    set -e
    for script in '$remote_stage'/persistent/rc.postsysinit '$remote_stage'/persistent/scripts/*.sh; do
        /bin/sh -n \"\$script\"
    done
    /usr/bin/awk -f '$remote_stage/persistent/scripts/parse-iwlist.awk' /dev/null >/dev/null
"

if [[ "$mode" == --stage-only ]]; then
    echo 'Code staged and validated; live files and cron were not changed.'
    exit 0
fi

rollback_stamp=$(date -u +%Y%m%dT%H%M%SZ)
activate=no
[[ "$mode" == --activate ]] && activate=yes

ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" "
    set -e
    persistent=/etc/persistent
    rollback=\"\$persistent/rollback/code-$rollback_stamp\"
    mkdir -p \"\$rollback/config\" \"\$rollback/scripts\"
    cp -p \"\$persistent/rc.postsysinit\" \"\$rollback/\"
    cp -p \"\$persistent/config/cron\" \"\$persistent/config/.profile\" \"\$rollback/config/\"
    cp -p \"\$persistent/scripts/\"*.sh \"\$persistent/scripts/\"*.awk \"\$rollback/scripts/\"

    pkill crond 2>/dev/null || true
    cp -p '$remote_stage/persistent/rc.postsysinit' \"\$persistent/rc.postsysinit.new\"
    mv \"\$persistent/rc.postsysinit.new\" \"\$persistent/rc.postsysinit\"
    for source in '$remote_stage'/persistent/config/*; do
        name=\${source##*/}
        cp -p \"\$source\" \"\$persistent/config/\$name.new\"
        mv \"\$persistent/config/\$name.new\" \"\$persistent/config/\$name\"
    done
    cp -p '$remote_stage/persistent/config/.profile' \"\$persistent/config/.profile.new\"
    mv \"\$persistent/config/.profile.new\" \"\$persistent/config/.profile\"
    cp -p \"\$persistent/config/.profile\" \"\$persistent/.profile.new\"
    mv \"\$persistent/.profile.new\" \"\$persistent/.profile\"
    for source in '$remote_stage'/persistent/scripts/*; do
        name=\${source##*/}
        cp -p \"\$source\" \"\$persistent/scripts/\$name.new\"
        chmod 750 \"\$persistent/scripts/\$name.new\"
        mv \"\$persistent/scripts/\$name.new\" \"\$persistent/scripts/\$name\"
    done
    chmod 750 \"\$persistent/rc.postsysinit\"
    /sbin/cfgmtd -w -p /etc/

    if [ '$activate' = yes ]; then
        /bin/sh \"\$persistent/rc.postsysinit\"
    else
        mkdir -p /tmp/ubnt-wifi
        : > /tmp/ubnt-wifi/paused
    fi
"

if [[ "$activate" == yes ]]; then
    echo "Installed and activated. Rollback: /etc/persistent/rollback/code-$rollback_stamp"
else
    echo "Installed paused with cron stopped. Rollback: /etc/persistent/rollback/code-$rollback_stamp"
fi
