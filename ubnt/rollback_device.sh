#!/bin/bash
set -euo pipefail

rollback_name=${1:-}
target=${2:-ubnt@192.168.8.20}

case $rollback_name in
    code-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
    *)
        echo "Usage: $0 code-YYYYMMDDTHHMMSSZ [user@host]" >&2
        exit 1
        ;;
esac

ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" "
    set -e
    persistent=/etc/persistent
    rollback=\"\$persistent/rollback/$rollback_name\"
    [ -f \"\$rollback/rc.postsysinit\" ]
    [ -f \"\$rollback/config/cron\" ]
    [ -f \"\$rollback/config/.profile\" ]
    [ -d \"\$rollback/scripts\" ]

    for script in \"\$rollback/rc.postsysinit\" \"\$rollback/scripts/\"*.sh; do
        /bin/sh -n \"\$script\"
    done

    pkill crond 2>/dev/null || true
    cp -p \"\$rollback/rc.postsysinit\" \"\$persistent/rc.postsysinit\"
    cp -p \"\$rollback/config/cron\" \"\$persistent/config/cron\"
    cp -p \"\$rollback/config/.profile\" \"\$persistent/config/.profile\"
    cp -p \"\$rollback/scripts/\"* \"\$persistent/scripts/\"
    chmod 750 \"\$persistent/rc.postsysinit\" \"\$persistent/scripts/\"*.sh
    /sbin/cfgmtd -w -p /etc/
    /bin/sh \"\$persistent/rc.postsysinit\"
"

echo "Restored and activated $rollback_name; profiles were not touched."
