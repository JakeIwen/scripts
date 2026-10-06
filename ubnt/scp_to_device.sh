#!/bin/bash
set -euo pipefail
umask 077

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
mode=${1:-}
target=ubnt@192.168.8.20
keep_rollbacks=2
max_bytes=114688
backup_pruned=no

usage() {
    echo "Usage: $0 --stage-only|--install-paused|--activate [user@host] [--keep-rollbacks N] [--max-bytes N] [--backup-pruned]" >&2
    exit 1
}

case $mode in
    --stage-only|--install-paused|--activate) shift ;;
    *) usage ;;
esac
have_target=no
while (( $# )); do
    case $1 in
        --keep-rollbacks|--max-bytes)
            [[ $# -ge 2 ]] || usage
            case $2 in ''|*[!0-9]*|0*) usage ;; esac
            if [[ $1 == --keep-rollbacks ]]; then
                [[ ${#2} -le 4 ]] || usage
                keep_rollbacks=$2
            else
                [[ ${#2} -le 6 && $2 -le 114688 ]] || usage
                max_bytes=$2
            fi
            shift 2
            ;;
        --backup-pruned) backup_pruned=yes; shift ;;
        -*) usage ;;
        *)
            [[ $have_target == no ]] || usage
            target=$1
            have_target=yes
            shift
            ;;
    esac
done

"$script_dir/backup_profiles.sh" "$target" --sync-working

stage_root=$(mktemp -d "${TMPDIR:-/tmp}/ubnt-code-stage.XXXXXX")
remote_stage="/tmp/${stage_root##*/}"

remote_owns_stage=no
cleanup() {
    rm -rf "$stage_root"
    if [[ $remote_owns_stage == no ]]; then
        ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" "rm -rf '$remote_stage'" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$stage_root/persistent/config" "$stage_root/persistent/scripts"
cp -p "$script_dir/persistent/rc.postsysinit" \
    "$script_dir/persistent/profile" \
    "$stage_root/persistent/"
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
/bin/sh -n "$stage_root/persistent/profile"

ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" "umask 077; mkdir '$remote_stage'"
scp -q -r -O -o BatchMode=yes -o ConnectTimeout=5 \
    "$stage_root/persistent" "$target:$remote_stage/"

ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" "
    set -e
    for script in '$remote_stage'/persistent/rc.postsysinit '$remote_stage'/persistent/profile '$remote_stage'/persistent/scripts/*.sh; do
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

remote_phase() {
    ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" \
        "/bin/sh -s -- '$1' '$remote_stage' 'code-$rollback_stamp' '$keep_rollbacks' '$max_bytes' '$activate' '$backup_pruned'" \
        < "$script_dir/deploy_remote.sh"
}

remote_phase prepare
if [[ $backup_pruned == yes ]]; then
    backup_dir=$(mktemp -d "$script_dir/private-backups/pruned-code-$rollback_stamp.XXXXXX")
    scp -q -O -o BatchMode=yes -o ConnectTimeout=5 \
        "$target:$remote_stage/pruned-rollbacks.tar.gz" \
        "$target:$remote_stage/pruned-rollbacks.md5" "$backup_dir/"
    expected=$(< "$backup_dir/pruned-rollbacks.md5")
    if command -v md5sum >/dev/null 2>&1; then
        actual=$(md5sum "$backup_dir/pruned-rollbacks.tar.gz")
        actual=${actual%% *}
    else
        actual=$(md5 -q "$backup_dir/pruned-rollbacks.tar.gz")
    fi
    [[ $expected =~ ^[0-9a-f]{32}$ && $actual == "$expected" ]] || {
        echo 'Pruned rollback backup checksum failed; live files and cron unchanged.' >&2
        exit 1
    }
    tar -tzf "$backup_dir/pruned-rollbacks.tar.gz" > /dev/null
    echo "Pruned rollback backup verified: $backup_dir"
fi

# Once install starts only its remote EXIT handler may remove the transaction.
# An SSH interruption is not proof that the remote worker has exited.
remote_owns_stage=yes
remote_phase install

if [[ "$activate" == yes ]]; then
    echo "Installed and activated. Rollback: /etc/persistent/rollback/code-$rollback_stamp"
else
    echo "Installed paused with cron stopped. Rollback: /etc/persistent/rollback/code-$rollback_stamp"
fi
