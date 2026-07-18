#!/bin/bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
mode=${1:---stage-only}
shift || true
subnets=("${@:-8}")

case $mode in
    --stage-only|--install-paused|--activate) ;;
    *)
        echo "Usage: $0 --stage-only|--install-paused|--activate [subnet ...]" >&2
        exit 1
        ;;
esac

for subnet in "${subnets[@]}"; do
    case $subnet in
        ''|*[!0-9]*)
            echo "Invalid subnet: $subnet" >&2
            exit 1
            ;;
    esac
    target="ubnt@192.168.$subnet.20"
    if ping -c 1 -W 1000 "192.168.$subnet.20" >/dev/null 2>&1; then
        "$script_dir/scp_to_device.sh" "$mode" "$target"
    else
        echo "Skipping unreachable $target" >&2
    fi
done
