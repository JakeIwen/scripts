#!/bin/bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
helper="$script_dir/usr/libexec/openwrt-5ghz-ap"
mode=${1:-}
target=${2:-root@192.168.6.1}

case $mode in
	--check) action=preflight ;;
	--apply) action=apply ;;
	--optimize) action=optimize ;;
	--remove) action=remove ;;
	--status) action=status ;;
	*)
		echo "Usage: $0 --check|--apply|--optimize|--remove|--status [root@host]" >&2
		exit 2
		;;
esac

case $target in
	*[!A-Za-z0-9_.:@%+-]*)
		echo "Refusing unsafe SSH target: $target" >&2
		exit 2
		;;
esac

/bin/sh -n "$helper"

remote_stage="/tmp/openwrt-5ghz-ap.$$"
case $remote_stage in
	/tmp/openwrt-5ghz-ap.[0-9]*) ;;
	*)
		echo "Refusing unexpected remote staging path: $remote_stage" >&2
		exit 1
		;;
esac

cleanup() {
	ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" \
		"/bin/rm -f '$remote_stage'" >/dev/null 2>&1 || true
}
trap cleanup EXIT HUP INT TERM

scp -q -O -o BatchMode=yes -o ConnectTimeout=5 \
	"$helper" "$target:$remote_stage"

ssh -o BatchMode=yes -o ConnectTimeout=5 "$target" \
	"/bin/sh -n '$remote_stage' && /bin/sh '$remote_stage' '$action'"
